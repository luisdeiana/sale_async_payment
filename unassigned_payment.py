import datetime
from decimal import Decimal

from sql import Literal, Null
from sql.functions import CurrentTimestamp

from trytond.exceptions import UserError
from trytond.model import ModelSQL, ModelView, fields
from trytond.pool import Pool
from trytond.transaction import Transaction
from trytond.wizard import Button, StateTransition, StateView, Wizard


# Offsets de ID para evitar colisión entre MP y QR en el modelo virtual.
# El id real del registro origen se preserva en source_id; el offset
# se aplica solo al `id` virtual del modelo para garantizar unicidad.
_MP_OFFSET = 10 ** 12
_QR_OFFSET = 2 * 10 ** 12


class UnassignedPayment(ModelSQL, ModelView):
    "Unassigned Payment"
    __name__ = 'sale.unassigned_payment'

    source = fields.Selection([
        ('mp', 'Mercado Pago'),
        ('qr', 'QR / Transfer'),
    ], "Source", readonly=True)
    source_id = fields.Integer("Source ID", readonly=True)
    reference = fields.Char("Reference", readonly=True)
    amount = fields.Numeric("Amount", digits=(16, 2), readonly=True)
    date = fields.Date("Date", readonly=True)
    payer_name = fields.Char("Payer", readonly=True)
    payer_cuit = fields.Char("Payer Tax ID", readonly=True)
    statement_line = fields.Many2One(
        'account.statement.line', "Statement Line", readonly=True)

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('date', 'DESC'), ('id', 'DESC')]
        cls._buttons.update({
            'link_to_sale': {},
        })

    @classmethod
    def table_query(cls):
        pool = Pool()
        MPTransaction = pool.get('account.payment.mp.transaction')
        QRDetection = pool.get('account.payment.qr.detection')
        AsyncPayment = pool.get('sale.async_payment')

        mp = MPTransaction.__table__()
        qr = QRDetection.__table__()
        ap = AsyncPayment.__table__()

        # Subqueries de async vinculados: ids de mp/qr ya con
        # async confirmed o pending (esos NO son huérfanos)
        mp_linked = ap.select(
            ap.mp_transaction,
            where=(ap.mp_transaction != Null))
        qr_linked = ap.select(
            ap.qr_detection,
            where=(ap.qr_detection != Null))

        mp_query = mp.select(
            (Literal(_MP_OFFSET) + mp.id).as_('id'),
            Literal(0).as_('create_uid'),
            CurrentTimestamp().as_('create_date'),
            Literal(Null).as_('write_uid'),
            Literal(Null).as_('write_date'),
            Literal('mp').as_('source'),
            mp.id.as_('source_id'),
            mp.mp_payment_id.as_('reference'),
            mp.amount.as_('amount'),
            mp.date_approved.cast('date').as_('date'),
            mp.payer_email.as_('payer_name'),
            mp.payer_identification_number.as_('payer_cuit'),
            mp.statement_line.as_('statement_line'),
            where=((mp.state == 'approved')
                & (mp.sale == Null)
                & (mp.invoice == Null)
                & ~mp.id.in_(mp_linked)))

        qr_query = qr.select(
            (Literal(_QR_OFFSET) + qr.id).as_('id'),
            Literal(0).as_('create_uid'),
            CurrentTimestamp().as_('create_date'),
            Literal(Null).as_('write_uid'),
            Literal(Null).as_('write_date'),
            Literal('qr').as_('source'),
            qr.id.as_('source_id'),
            qr.bank_reference.as_('reference'),
            qr.amount.as_('amount'),
            qr.detection_date.cast('date').as_('date'),
            qr.payer_name.as_('payer_name'),
            qr.payer_cuit.as_('payer_cuit'),
            qr.statement_line.as_('statement_line'),
            where=((qr.state == 'confirmed')
                & (qr.sale == Null)
                & ~qr.id.in_(qr_linked)))

        return mp_query | qr_query

    # ── Helpers testables ───────────────────────────────────────────────

    @classmethod
    def _resolve_source(cls, virtual_id):
        # virtual_id → (source, source_id)
        vid = int(virtual_id)
        if vid >= _QR_OFFSET:
            return 'qr', vid - _QR_OFFSET
        return 'mp', vid - _MP_OFFSET

    @classmethod
    def _build_async_vals(cls, unassigned, sale, now):
        # Construye el dict de vals para crear sale.async_payment
        # en estado 'confirmed' desde un huérfano vinculado a una venta.
        source_record = cls._get_source_record(
            unassigned.source, unassigned.source_id)
        journal_id = (
            source_record.config.journal.id
            if source_record.config and source_record.config.journal
            else None)
        vals = {
            'sale': sale.id,
            'amount': unassigned.amount,
            'received_amount': unassigned.amount,
            'journal': journal_id,
            'payment_method': (
                'mp_link' if unassigned.source == 'mp'
                else 'bank_transfer'),
            'state': 'confirmed',
            'statement_line': (
                unassigned.statement_line.id
                if unassigned.statement_line else None),
            'confirmed_by': Transaction().user,
            'confirmed_date': now,
            'matched_date': now,
            'match_criteria': 'manual',
            'payer_name': unassigned.payer_name,
            'payer_cuit': unassigned.payer_cuit,
        }
        if unassigned.source == 'mp':
            vals['mp_transaction'] = unassigned.source_id
            vals['mp_payment_id'] = unassigned.reference
        else:
            vals['qr_detection'] = unassigned.source_id
            vals['bank_reference'] = unassigned.reference
        return vals

    @classmethod
    def _source_model_name(cls, source):
        return ('account.payment.mp.transaction' if source == 'mp'
            else 'account.payment.qr.detection')

    @classmethod
    def _get_source_record(cls, source, source_id):
        Cls = Pool().get(cls._source_model_name(source))
        return Cls(source_id)

    @classmethod
    def _set_source_sale(cls, source, source_id, sale_id):
        # Helper testable: vincula sale en el record origen (MP o QR).
        Cls = Pool().get(cls._source_model_name(source))
        Cls.write([Cls(source_id)], {'sale': sale_id})

    # ── Botón ───────────────────────────────────────────────────────────

    @classmethod
    @ModelView.button_action('sale_async_payment.wizard_link_unassigned')
    def link_to_sale(cls, unassigned_payments):
        pass


class LinkUnassignedPaymentForm(ModelView):
    "Link Unassigned Payment to Sale"
    __name__ = 'sale.unassigned_payment.link.form'

    sale = fields.Many2One(
        'sale.sale', "Sale", required=True,
        domain=[('state', 'in', [
            'draft', 'quotation', 'confirmed', 'processing'])])
    payment_amount = fields.Numeric(
        "Payment Amount", digits=(16, 2), readonly=True)
    payment_source = fields.Char("Source", readonly=True)
    payment_reference = fields.Char("Reference", readonly=True)
    payer_name = fields.Char("Payer", readonly=True)


class LinkUnassignedPayment(Wizard):
    "Link Unassigned Payment"
    __name__ = 'sale.unassigned_payment.link'

    start = StateView('sale.unassigned_payment.link.form',
        'sale_async_payment.link_unassigned_form_view', [
            Button("Cancel", 'end', 'tryton-cancel'),
            Button("Link", 'link_', 'tryton-ok', default=True),
        ])
    link_ = StateTransition()

    def default_start(self, fields):
        UnassignedPayment = Pool().get('sale.unassigned_payment')
        unassigned = self.record
        if not unassigned:
            return {}
        labels = dict(UnassignedPayment.source.selection)
        return {
            'payment_amount': unassigned.amount,
            'payment_source': labels.get(unassigned.source, unassigned.source),
            'payment_reference': unassigned.reference or '',
            'payer_name': unassigned.payer_name or '',
        }

    def transition_link_(self):
        pool = Pool()
        UnassignedPayment = pool.get('sale.unassigned_payment')
        AsyncPayment = pool.get('sale.async_payment')
        StatementLine = pool.get('account.statement.line')

        unassigned = self.record
        if not unassigned:
            return 'end'
        sale = self.start.sale
        if not sale:
            raise UserError("Select a sale.")

        now = datetime.datetime.now()
        vals = UnassignedPayment._build_async_vals(unassigned, sale, now)
        if not vals.get('journal'):
            raise UserError(
                "Could not determine the payment journal. Check the "
                "source configuration.")

        async_payment = AsyncPayment.create([vals])[0]

        # Vincular sale en el registro origen (mp.transaction o qr.detection)
        UnassignedPayment._set_source_sale(
            unassigned.source, unassigned.source_id, sale.id)

        # Vincular sale en la statement.line y registrar diferencia
        if unassigned.statement_line:
            line = StatementLine(unassigned.statement_line.id)
            line_vals = {}
            if not line.sale:
                line_vals['sale'] = sale.id
            received = unassigned.amount or Decimal('0')
            expected = sale.total_amount or Decimal('0')
            diff = received - expected
            if diff != 0:
                line_vals['unmatched_difference'] = diff
            if line_vals:
                StatementLine.write([line], line_vals)

        return 'end'
