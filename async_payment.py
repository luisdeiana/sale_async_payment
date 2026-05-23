import datetime
from decimal import Decimal

from sql import For, Literal

from trytond.exceptions import UserError, UserWarning
from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Workflow, fields)
from trytond.pool import Pool
from trytond.pyson import Eval, If
from trytond.rpc import RPC
from trytond.transaction import Transaction


def _user_is_payment_supervisor():
    pool = Pool()
    User = pool.get('res.user')
    ModelData = pool.get('ir.model.data')
    user = User(Transaction().user)
    try:
        group_id = ModelData.get_id(
            'account_payment_methods', 'group_payment_supervisor')
    except Exception:
        return False
    return any(g.id == group_id for g in user.groups)


PAYMENT_METHODS = [
    ('mp_link', 'Mercado Pago Link'),
    ('mp_qr_static', 'Mercado Pago Static QR'),
    ('bank_transfer', 'Bank Transfer'),
    ('other', 'Other'),
]

# Lista blanca de payment_methods de account.statement.journal que
# admiten cobros asíncronos. Para sumar un medio de pago nuevo
# (openpay/viumi/etc.) agregarlo acá Y mapearlo en
# JOURNAL_ASYNC_METHODS.
ASYNC_CAPABLE_METHODS = ('mercadopago', 'bank_polling')

# Métodos asíncronos ofrecidos por cada journal.payment_method.
# El cajero elige cuál usar en el wizard. Cuando se sume un medio
# nuevo (openpay/viumi/etc.) agregar su entrada acá. Si la lista
# tiene un solo método, el wizard lo pre-selecciona y lo deja
# readonly.
JOURNAL_ASYNC_METHODS = {
    'mercadopago': ['mp_link', 'bank_transfer'],
    'bank_polling': ['bank_transfer'],
}

# Human-readable labels for error messages about compatible methods
ASYNC_CAPABLE_METHOD_LABELS = {
    'mercadopago': 'Mercado Pago',
    'bank_polling': 'Bank Transfer',
}

STATES = [
    ('pending', 'Pending'),
    ('suggested', 'Suggested'),
    ('confirmed', 'Confirmed'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]

MATCH_CRITERIA = [
    ('mp_payment_id', 'MP Payment ID'),
    ('bank_reference', 'Bank Reference'),
    ('payer_cuit', 'Payer Tax ID'),
    ('amount_exact', 'Exact Amount'),
    ('manual', 'Manual Link'),
]


class AsyncPayment(Workflow, ModelSQL, ModelView):
    "Async Payment"
    __name__ = 'sale.async_payment'

    company = fields.Many2One(
        'company.company', "Company", required=True, ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'pending'})
    sale = fields.Many2One(
        'sale.sale', "Sale", required=True, ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'pending'})
    amount = fields.Numeric(
        "Amount", digits=(16, 2), required=True,
        states={'readonly': Eval('state') != 'pending'})
    journal = fields.Many2One(
        'account.statement.journal', "Statement Journal", required=True,
        states={'readonly': Eval('state') != 'pending'})
    payment_method = fields.Selection(
        PAYMENT_METHODS, "Payment Method", required=True,
        states={'readonly': Eval('state') != 'pending'})
    shop = fields.Many2One(
        'sale.shop', "Shop",
        states={'readonly': Eval('state') != 'pending'})
    notes = fields.Text(
        "Notes",
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    state = fields.Selection(
        STATES, "State", readonly=True, required=True)
    expiration_date = fields.DateTime(
        "Expiration Date",
        states={'readonly': Eval('state') != 'pending'})
    expiration_date_date = fields.Function(
        fields.Date("Expires"),
        'get_expiration_date_date')

    # Received payment data (filled when suggested/confirmed)
    received_amount = fields.Numeric(
        "Received Amount", digits=(16, 2),
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    mp_payment_id = fields.Char(
        "MP Payment ID",
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    bank_reference = fields.Char(
        "Bank Reference",
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    payer_name = fields.Char(
        "Payer Name",
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    payer_cuit = fields.Char(
        "Payer Tax ID",
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    match_criteria = fields.Selection(
        MATCH_CRITERIA + [('', '')], "Match Criteria", sort=False,
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    matched_date = fields.DateTime(
        "Match Date",
        states={'readonly': True})

    # External transactions link
    mp_transaction = fields.Many2One(
        'account.payment.mp.transaction', "MP Transaction",
        ondelete='SET NULL',
        states={'invisible': Eval('payment_method') != 'mp_link',
                'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    qr_detection = fields.Many2One(
        'account.payment.qr.detection', "QR Detection",
        ondelete='SET NULL',
        states={'invisible': Eval('payment_method') != 'bank_transfer',
                'readonly': Eval('state').in_(['confirmed', 'cancelled'])})

    # Confirmation result
    statement_line = fields.Many2One(
        'account.statement.line', "Statement Line",
        readonly=True, ondelete='SET NULL')
    confirmed_by = fields.Many2One(
        'res.user', "Confirmed By", readonly=True)
    confirmed_date = fields.DateTime(
        "Confirmation Date", readonly=True)

    # Transiciones de estados válidas
    _transitions = {
        ('pending', 'suggested'),
        ('pending', 'confirmed'),
        ('pending', 'expired'),
        ('pending', 'cancelled'),
        ('suggested', 'confirmed'),
        ('suggested', 'pending'),
        ('suggested', 'expired'),
        ('suggested', 'cancelled'),
        ('expired', 'pending'),
    }

    @classmethod
    def __setup__(cls):
        super().__setup__()
        cls._order = [('id', 'DESC')]
        cls._transitions |= {
            ('pending', 'suggested'),
            ('pending', 'confirmed'),
            ('pending', 'expired'),
            ('pending', 'cancelled'),
            ('suggested', 'confirmed'),
            ('suggested', 'pending'),
            ('suggested', 'expired'),
            ('suggested', 'cancelled'),
            ('expired', 'pending'),
        }
        cls.__rpc__.update({
            'find_candidate': RPC(readonly=False, instantiate=None),
        })
        cls._buttons.update({
            'confirm': {
                'invisible': ~Eval('state').in_(['pending', 'suggested']),
                'depends': ['state'],
            },
            'cancel': {
                'invisible': Eval('state').in_(
                    ['cancelled', 'expired', 'confirmed']),
                'depends': ['state'],
            },
            'reject_suggestion': {
                'invisible': Eval('state') != 'suggested',
                'depends': ['state'],
            },
            'reactivate': {
                'invisible': Eval('state') != 'expired',
                'depends': ['state'],
            },
        })

    @staticmethod
    def default_state():
        return 'pending'

    @staticmethod
    def default_match_criteria():
        return ''

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    @classmethod
    def __register__(cls, module_name):
        # Backfill `company` desde sale.company para filas previas a la
        # introducción del campo (required=True). Se ejecuta antes de
        # que Tryton aplique constraints sobre la columna recién creada.
        pool = Pool()
        table_h = cls.__table_handler__(module_name)
        column_missing = not table_h.column_exist('company')
        super().__register__(module_name)
        if column_missing:
            cursor = Transaction().connection.cursor()
            ap = cls.__table__()
            Sale = pool.get('sale.sale')
            sale_t = Sale.__table__()
            cursor.execute(*ap.update(
                columns=[ap.company],
                values=[sale_t.company],
                from_=[sale_t],
                where=(ap.sale == sale_t.id) & (ap.company == None)))

    def get_expiration_date_date(self, name):
        if self.expiration_date:
            return self.expiration_date.date()
        return None

    def get_rec_name(self, name):
        sale_name = self.sale.rec_name if self.sale else ''
        method = dict(PAYMENT_METHODS).get(self.payment_method, '')
        return f'{sale_name} — {method}'

    # ── Domain dinámico por user_filter (paso 9) ────────────────────────

    @classmethod
    def _get_user_filter_domain(cls, user_filter, is_supervisor, user_id):
        # Retorna lista de tuplas de domain (AND) a aplicar al search
        # según el user_filter del usuario actual.
        # - Root (user_id == 0) y supervisor ven todo.
        # - Sin filter configurado: ven todo.
        # - Con shops: limita a esas sucursales.
        # - only_own: limita a registros creados por el usuario.
        if user_id == 0 or is_supervisor or user_filter is None:
            return []
        extra = []
        if user_filter.shops:
            extra.append(('shop', 'in', [s.id for s in user_filter.shops]))
        if user_filter.only_own:
            extra.append(('create_uid', '=', user_id))
        return extra

    @classmethod
    def search(cls, domain, *args, **kwargs):
        pool = Pool()
        UserFilter = pool.get('sale.async_payment.user_filter')
        user_id = Transaction().user
        company_id = Transaction().context.get('company')
        is_supervisor = _user_is_payment_supervisor()
        user_filter = None
        if user_id != 0 and not is_supervisor:
            uf_domain = [('user', '=', user_id)]
            if company_id:
                uf_domain.append(('company', '=', company_id))
            filters = UserFilter.search(uf_domain, limit=1)
            if filters:
                user_filter = filters[0]
        extra = cls._get_user_filter_domain(
            user_filter, is_supervisor, user_id)
        if extra:
            domain = ['AND', domain, *extra]
        return super().search(domain, *args, **kwargs)

    # ── find_candidate (paso 11) — matching de pagos entrantes ──────────

    @classmethod
    def _normalize_cuit(cls, cuit):
        if not cuit:
            return ''
        return ''.join(c for c in str(cuit) if c.isdigit())

    @classmethod
    def _build_match_domains(cls, payment_data, amount):
        # Lista ordenada de (domain, match_criteria). El consumidor
        # itera y para en el primero que encuentra resultados.
        # Orden: más específico → más amplio.
        domains = []

        mp_id = payment_data.get('mp_payment_id')
        if mp_id:
            domains.append((
                [('state', 'in', ['pending', 'suggested']),
                 ('mp_payment_id', '=', mp_id)],
                'mp_payment_id'))

        bank_ref = payment_data.get('bank_reference')
        if bank_ref:
            domains.append((
                [('state', 'in', ['pending', 'suggested']),
                 ('bank_reference', '=', bank_ref)],
                'bank_reference'))

        payer_cuit = payment_data.get('payer_cuit')
        if payer_cuit:
            clean = cls._normalize_cuit(payer_cuit)
            if clean:
                domains.append((
                    [('state', '=', 'pending'),
                     ('sale.party.tax_identifier.code', '=', clean)],
                    'payer_cuit'))

        if amount is not None:
            domains.append((
                [('state', '=', 'pending'),
                 ('amount', '=', amount)],
                'amount_exact'))

        return domains

    @classmethod
    def _lock_for_update(cls, ids):
        if not ids:
            return
        transaction = Transaction()
        database = transaction.database
        connection = transaction.connection
        if database.has_select_for():
            table = cls.__table__()
            query = table.select(
                Literal(1),
                where=table.id.in_(list(ids)),
                for_=For('UPDATE', nowait=True))
            with connection.cursor() as cursor:
                cursor.execute(*query)
        else:
            cls.lock()

    @classmethod
    def find_candidate(cls, payment_data):
        # Retorna dict {'matched': bool, 'async_id': int|None,
        # 'match_criteria': str|None, 'reason': str|None}.
        # Pensado para ser llamado desde el webhook MP y el poller
        # IMAP del Motor IA vía XML-RPC. Marca async pending como
        # suggested cuando encuentra match único.
        if not isinstance(payment_data, dict):
            return {
                'matched': False, 'async_id': None,
                'match_criteria': None, 'reason': 'invalid_payload'}

        raw_amount = payment_data.get('amount')
        try:
            amount = (
                Decimal(str(raw_amount)) if raw_amount is not None
                else None)
        except Exception:
            amount = None

        domains = cls._build_match_domains(payment_data, amount)
        if not domains:
            return {
                'matched': False, 'async_id': None,
                'match_criteria': None, 'reason': 'no_data'}

        # Refinar por amount cuando el tier por CUIT da varios:
        # si el monto coincide con alguno, ese gana.
        for domain, criteria in domains:
            matches = cls.search(domain)
            if not matches:
                continue
            if len(matches) > 1 and amount is not None:
                exact = [m for m in matches if m.amount == amount]
                if len(exact) == 1:
                    matches = exact
            if len(matches) > 1:
                return {
                    'matched': False, 'async_id': None,
                    'match_criteria': criteria, 'reason': 'ambiguous',
                    'candidate_count': len(matches)}
            candidate = matches[0]
            cls._lock_for_update([candidate.id])
            now = datetime.datetime.now()
            write_vals = {
                'received_amount': amount,
                'match_criteria': criteria,
                'matched_date': now,
            }
            if payment_data.get('mp_payment_id'):
                write_vals['mp_payment_id'] = payment_data['mp_payment_id']
            if payment_data.get('bank_reference'):
                write_vals['bank_reference'] = (
                    payment_data['bank_reference'])
            if payment_data.get('payer_name'):
                write_vals['payer_name'] = payment_data['payer_name']
            if payment_data.get('payer_cuit'):
                write_vals['payer_cuit'] = payment_data['payer_cuit']
            # Pending → suggested. Si ya estaba suggested, solo
            # actualizar los datos (idempotente).
            if candidate.state == 'pending':
                write_vals['state'] = 'suggested'
            cls.write([candidate], write_vals)
            return {
                'matched': True, 'async_id': candidate.id,
                'match_criteria': criteria, 'reason': None}

        return {
            'matched': False, 'async_id': None,
            'match_criteria': None, 'reason': 'no_candidate'}

    # ── Helpers testables ───────────────────────────────────────────────

    @classmethod
    def _compute_received_and_diff(cls, async_payment):
        # Si no se completó received_amount, asumir que entró el monto
        # exacto registrado en el async. Retorna (received, diff) donde
        # diff = received - amount (positivo=sobrante, negativo=faltante).
        amount = async_payment.amount or Decimal('0')
        received = async_payment.received_amount
        if received is None:
            received = amount
        diff = received - amount
        return received, diff

    # ── Cron de expiración (paso 12) ────────────────────────────────────

    @classmethod
    def _expire_domain(cls, now):
        # Helper testeable: domain para encontrar async expirables.
        return [
            ('state', 'in', ['pending', 'suggested']),
            ('expiration_date', '!=', None),
            ('expiration_date', '<=', now),
        ]

    @classmethod
    def expire_cron(cls):
        now = datetime.datetime.now()
        to_expire = cls.search(cls._expire_domain(now))
        if to_expire:
            cls.write(list(to_expire), {'state': 'expired'})

    # ── Transiciones (botones) ──────────────────────────────────────────

    @classmethod
    @ModelView.button
    @Workflow.transition('confirmed')
    def confirm(cls, async_payments):
        pool = Pool()
        StatementLine = pool.get('account.statement.line')
        Journal = pool.get('account.statement.journal')
        Date = pool.get('ir.date')
        Warning_ = pool.get('res.user.warning')
        Sale = pool.get('sale.sale')

        today = Date.today()
        now = datetime.datetime.now()
        user_id = Transaction().user

        sales_to_check = set()

        for ap in async_payments:
            if ap.state not in ('pending', 'suggested'):
                raise UserError(
                    "Only payments in Pending or Suggested state can be "
                    "confirmed. Current state: " + ap.state)

            received, diff = cls._compute_received_and_diff(ap)

            if diff != 0:
                key = 'sale_async_payment.diff_%d' % ap.id
                if Warning_.check(key):
                    raise UserWarning(
                        key,
                        f"Received ${received}, expected ${ap.amount}, "
                        f"difference ${diff}. Confirm the line anyway "
                        f"with the recorded difference?")

            stmt_id = ap.journal.get_or_create_statement_for_date(today)
            party = ap.sale.party
            with Transaction().set_context(date=today):
                account = party.account_receivable_used
            if not account:
                raise UserError(
                    "Party '%s' has no receivable account configured."
                    % party.name)

            description = (
                ap.sale.number or ap.sale.reference or str(ap.sale.id))
            line = StatementLine(
                statement=stmt_id,
                date=today,
                amount=received,
                party=party.id,
                account=account.id,
                description=description,
                sale=ap.sale.id,
                unmatched_difference=diff,
            )
            line.save()

            ap.statement_line = line
            ap.received_amount = received
            ap.confirmed_by = user_id
            ap.confirmed_date = now
            ap.save()

            sales_to_check.add(ap.sale.id)

        # Trigger workflow_to_end por cada sale donde el total quedó
        # cubierto exactamente por las statement.line.
        cls._maybe_trigger_workflow_to_end(sales_to_check)

    @classmethod
    def _maybe_trigger_workflow_to_end(cls, sale_ids):
        # Helper testable. Llama Sale.workflow_to_end sobre las ventas
        # que cumplen total_amount == paid_amount Y estado en draft/
        # quotation/confirmed/processing.
        if not sale_ids:
            return
        pool = Pool()
        Sale = pool.get('sale.sale')
        sales = Sale.browse(list(sale_ids))
        to_end = [
            s for s in sales
            if s.total_amount
            and s.total_amount == s.paid_amount
            and s.state in ('draft', 'quotation', 'confirmed',
                             'processing')]
        if to_end:
            Sale.workflow_to_end(to_end)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, async_payments):
        for ap in async_payments:
            if ap.state == 'confirmed':
                # Cancelling a confirmed payment implies reverting the
                # statement.line and accounting effects — out of scope
                # for step 7. When implemented, will require Supervisor
                # role (group_payment_supervisor).
                raise UserError(
                    "Cannot cancel a Confirmed payment from this view. "
                    "Revert the statement line manually and create a "
                    "new async payment.")
            if ap.state in ('cancelled', 'expired'):
                raise UserError(
                    "Payment is already in state " + ap.state)

    @classmethod
    @ModelView.button
    @Workflow.transition('pending')
    def reject_suggestion(cls, async_payments):
        for ap in async_payments:
            if ap.state != 'suggested':
                raise UserError(
                    "Only payments in Suggested state can be rejected.")
        cls.write(list(async_payments), {
            'mp_payment_id': None,
            'bank_reference': None,
            'payer_name': None,
            'payer_cuit': None,
            'received_amount': None,
            'match_criteria': '',
            'matched_date': None,
        })

    @classmethod
    @ModelView.button
    @Workflow.transition('pending')
    def reactivate(cls, async_payments):
        if not _user_is_payment_supervisor():
            raise UserError(
                "Reactivating an expired payment requires the "
                "\"Payment Supervisor\" role.")
        for ap in async_payments:
            if ap.state != 'expired':
                raise UserError(
                    "Only Expired payments can be reactivated.")
