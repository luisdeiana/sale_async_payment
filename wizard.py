from decimal import Decimal

from trytond.exceptions import UserError
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.wizard import Button, StateTransition, StateView

from .async_payment import PAYMENT_METHODS


# Métodos visibles en el ChoiceForm según tipo de journal
# (payment_method del journal → {método async: nombre del toggle en la config})
_JOURNAL_METHOD_MAP = {
    'mercadopago': {
        'mp_link': 'enable_async_link',
        'bank_transfer': 'enable_async_transfer',
    },
    'bank_polling': {
        'bank_transfer': 'enable_async_bank_transfer',
    },
}


def _get_journal_async_flags(journal):
    if not journal:
        return {}
    pm = getattr(journal, 'payment_method', None)
    if pm not in _JOURNAL_METHOD_MAP:
        return {}
    pool = Pool()
    model_name = ('account.payment.mp.config' if pm == 'mercadopago'
        else 'account.payment.qr.config')
    Config = pool.get(model_name)
    configs = Config.search(
        [('journal', '=', journal.id), ('active', '=', True)], limit=1)
    if not configs:
        return {}
    config = configs[0]
    return {
        method: bool(getattr(config, flag, False))
        for method, flag in _JOURNAL_METHOD_MAP[pm].items()
    }


class SalePaymentForm(metaclass=PoolMeta):
    __name__ = 'sale.payment.form'

    has_async_options = fields.Function(
        fields.Boolean('Opciones asíncronas disponibles'),
        'on_change_with_has_async_options')

    @fields.depends('journal')
    def on_change_with_has_async_options(self, name=None):
        if not self.journal:
            return False
        # Si el journal está soportado (MP o QR), el botón aparece — el
        # método 'other' está siempre disponible como fallback manual.
        pm = getattr(self.journal, 'payment_method', None)
        return pm in _JOURNAL_METHOD_MAP


class AsyncMethodSelectForm(ModelView):
    'Selección de método de cobro asíncrono'
    __name__ = 'sale.async.method.select.form'

    payment_method = fields.Selection(
        [('', '')] + list(PAYMENT_METHODS),
        'Método de cobro', required=True, sort=False)
    payment_amount = fields.Numeric(
        'Monto a registrar', digits=(16, 2), required=True)
    notes = fields.Text('Notas')

    has_mp_link = fields.Boolean('MP Link habilitado', readonly=True)
    has_bank_transfer = fields.Boolean(
        'Transferencia habilitada', readonly=True)


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
        flags = _get_journal_async_flags(journal)
        amount = self.start.payment_amount or Decimal('0.0')
        return {
            'payment_method': '',
            'payment_amount': amount,
            'has_mp_link': flags.get('mp_link', False),
            'has_bank_transfer': flags.get('bank_transfer', False),
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
    def _async_validate_method(cls, method, flags):
        if not method:
            raise UserError(
                'Debe seleccionar un método de cobro asíncrono.')
        if method == 'other':
            return
        if not flags.get(method, False):
            raise UserError(
                'El método elegido no está habilitado para este '
                'diario. Activarlo en Configuración → Opciones de '
                'cobro asíncrono.')

    def transition_async_register(self):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        Sale = pool.get('sale.sale')

        sale = self.record
        if sale is None:
            return 'end'

        form = self.async_method_select
        method = form.payment_method
        amount = form.payment_amount
        journal = self.start.journal

        if amount is None or amount <= 0:
            raise UserError('El monto debe ser mayor a cero.')

        flags = _get_journal_async_flags(journal)
        self._async_validate_method(method, flags)

        shop_id = sale.shop.id if getattr(sale, 'shop', None) else None
        vals = {
            'sale': sale.id,
            'amount': amount,
            'journal': journal.id,
            'payment_method': method,
            'notes': form.notes or None,
            'state': 'pending',
            'shop': shop_id,
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
