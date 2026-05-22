from decimal import Decimal

from trytond.exceptions import UserError
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.wizard import Button, StateTransition, StateView

from .async_payment import (
    ASYNC_CAPABLE_METHODS, ASYNC_CAPABLE_METHOD_LABELS,
    JOURNAL_TO_ASYNC_METHOD, PAYMENT_METHODS)


def _journal_has_async_config(journal):
    # True si el journal tiene una línea en sale.async_payment.config.line.
    # La grilla es la única fuente de la verdad — sin línea, no hay async.
    if not journal:
        return False
    pm = getattr(journal, 'payment_method', None)
    if pm not in ASYNC_CAPABLE_METHODS:
        return False
    pool = Pool()
    ConfigLine = pool.get('sale.async_payment.config.line')
    lines = ConfigLine.search(
        [('journal', '=', journal.id)], limit=1)
    return bool(lines)


def _derive_async_method(journal):
    # Mapea journal.payment_method al payment_method del async_payment.
    # Sin mapeo definido, cae a 'other' (registro manual sin auto-confirm).
    pm = getattr(journal, 'payment_method', None) if journal else None
    return JOURNAL_TO_ASYNC_METHOD.get(pm, 'other')


class SalePaymentForm(metaclass=PoolMeta):
    __name__ = 'sale.payment.form'

    has_async_options = fields.Function(
        fields.Boolean('Opciones asíncronas disponibles'),
        'on_change_with_has_async_options')

    @fields.depends('journal')
    def on_change_with_has_async_options(self, name=None):
        # Solo True si hay línea de config para este journal.
        return _journal_has_async_config(self.journal)


class AsyncMethodSelectForm(ModelView):
    'Selección de método de cobro asíncrono'
    __name__ = 'sale.async.method.select.form'

    journal = fields.Many2One(
        'account.statement.journal', 'Diario',
        readonly=True,
        help='Diario de cobro asíncrono (viene del wizard de pago).')
    journal_payment_method = fields.Char(
        'Método del diario', readonly=True)
    async_method_label = fields.Char(
        'Método asíncrono', readonly=True,
        help='Tipo de cobro asíncrono que se va a registrar, '
             'derivado del método de pago del diario.')
    payment_amount = fields.Numeric(
        'Monto a registrar', digits=(16, 2), required=True)
    notes = fields.Text('Notas')


class AsyncConfirmForm(ModelView):
    'Resultado del registro de cobro asíncrono'
    __name__ = 'sale.async.confirm.form'

    payment_method_label = fields.Char('Método', readonly=True)
    amount = fields.Numeric(
        'Monto registrado', digits=(16, 2), readonly=True)
    payment_url = fields.Char(
        'URL de pago', readonly=True,
        help='Compartí este link con el cliente.')
    message = fields.Text('Mensaje', readonly=True)


class WizardSalePayment(metaclass=PoolMeta):
    __name__ = 'sale.payment'

    # Tryton hace deepcopy de los StateView buscando el "último" en MRO,
    # lo que ignora cualquier redeclaración en una subclase. La forma
    # correcta de insertar botones nuevos en 'start' es modificar
    # start.buttons desde __setup__ después de llamar al super.

    async_method_select = StateView('sale.async.method.select.form',
        'sale_async_payment.async_method_select_view_form', [
            Button('Volver', 'start', 'tryton-back'),
            Button('Registrar', 'async_register',
                'tryton-ok', default=True),
        ])

    async_register = StateTransition()

    async_confirm = StateView('sale.async.confirm.form',
        'sale_async_payment.async_confirm_view_form', [
            Button('Cerrar', 'end', 'tryton-ok', default=True),
        ])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        # Insertar "Pago asíncrono" entre el botón Cancel y el Pay del
        # state 'start' del wizard base.
        async_button = Button(
            'Pago asíncrono', 'async_method_select', 'tryton-launch',
            states={
                'invisible': ~Bool(Eval('has_async_options', False))})
        buttons = list(cls.start.buttons)
        # Si ya está agregado (re-setup), no duplicar.
        if not any(getattr(b, 'state', None) == 'async_method_select'
                   for b in buttons):
            buttons.insert(len(buttons) - 1, async_button)
            cls.start.buttons = buttons

    def default_async_method_select(self, fields):
        journal = self.start.journal
        amount = self.start.payment_amount or Decimal('0.0')
        derived = _derive_async_method(journal)
        async_label = dict(PAYMENT_METHODS).get(derived, derived)
        journal_pm = getattr(journal, 'payment_method', None) if journal else None
        journal_pm_label = ASYNC_CAPABLE_METHOD_LABELS.get(
            journal_pm, journal_pm or '')
        return {
            'journal': journal.id if journal else None,
            'journal_payment_method': journal_pm_label,
            'async_method_label': async_label,
            'payment_amount': amount,
            'notes': None,
        }

    @classmethod
    def _async_compute_next_state(cls, total, paid, pending_after_register):
        total = total or Decimal('0')
        paid = paid or Decimal('0')
        pending = pending_after_register or Decimal('0')
        residual = total - paid - pending
        return 'async_confirm' if residual <= 0 else 'start'

    @classmethod
    def _async_validate_journal(cls, journal):
        # El botón asíncrono solo aparece si el journal tiene línea.
        # Re-validamos acá por defensa contra cambios post-render.
        if not _journal_has_async_config(journal):
            raise UserError(
                "El diario '%s' no tiene cobros asíncronos habilitados. "
                "Configurarlo en Configuración → Cobros asíncronos."
                % (getattr(journal, 'name', '?') if journal else '?'))

    def transition_async_register(self):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        Sale = pool.get('sale.sale')

        sale = self.record
        if sale is None:
            return 'end'

        form = self.async_method_select
        amount = form.payment_amount
        journal = self.start.journal

        if amount is None or amount <= 0:
            raise UserError('El monto debe ser mayor a cero.')

        self._async_validate_journal(journal)
        method = _derive_async_method(journal)

        shop_id = sale.shop.id if getattr(sale, 'shop', None) else None
        Config = pool.get('sale.async_payment.config')
        config = Config(1)
        expiration_date = config.compute_expiration_date(journal)
        vals = {
            'sale': sale.id,
            'amount': amount,
            'journal': journal.id,
            'payment_method': method,
            'notes': form.notes or None,
            'state': 'pending',
            'shop': shop_id,
            'expiration_date': expiration_date,
        }
        async_payment = AsyncPayment.create([vals])[0]

        if method == 'mp_link':
            MPConfig = pool.get('account.payment.mp.config')
            mp_transaction = MPConfig.create_checkout_pro(sale, 'sale.sale')
            AsyncPayment.write([async_payment], {
                'mp_transaction': mp_transaction.id})

        sale = Sale(sale.id)
        return self._async_compute_next_state(
            sale.total_amount, sale.paid_amount, sale.async_pending_amount)

    def default_async_confirm(self, fields):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        sale = self.record
        if sale is None:
            return {}
        aps = AsyncPayment.search(
            [('sale', '=', sale.id), ('state', '=', 'pending')],
            order=[('id', 'DESC')], limit=1)
        if not aps:
            return {}
        ap = aps[0]
        label = dict(PAYMENT_METHODS).get(
            ap.payment_method, ap.payment_method)
        url = ''
        if ap.mp_transaction:
            url = ap.mp_transaction.payment_url or ''
        if url:
            message = ('Cobro asíncrono registrado. Compartí el link '
                'de pago con el cliente. Cuando confirme, el sistema '
                'lo procesará automáticamente.')
        elif ap.payment_method == 'mp_link':
            message = ('Cobro registrado, pero no se obtuvo URL del '
                'link. Revisar la configuración de Mercado Pago.')
        else:
            message = ('Cobro asíncrono registrado en estado '
                'Pendiente. Se confirmará cuando llegue el pago.')
        return {
            'payment_method_label': label,
            'amount': ap.amount,
            'payment_url': url,
            'message': message,
        }
