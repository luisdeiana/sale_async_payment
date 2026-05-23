from decimal import Decimal

from trytond.exceptions import UserError
from trytond.model import ModelView, fields
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Bool, Eval
from trytond.wizard import Button, StateTransition, StateView

from .async_payment import ASYNC_CAPABLE_METHODS, PAYMENT_METHODS


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


def _async_methods_for_journal(journal):
    # Métodos habilitados explícitamente por el usuario en
    # sale.async_payment.config.line para este journal. La whitelist
    # técnica JOURNAL_ASYNC_METHODS sólo decide qué se puede agregar
    # a la grilla; lo que se OFRECE al cajero es lo que el usuario
    # efectivamente habilitó.
    if not journal:
        return []
    pool = Pool()
    ConfigLine = pool.get('sale.async_payment.config.line')
    lines = ConfigLine.search(
        [('journal', '=', journal.id)],
        order=[('sequence', 'ASC'), ('id', 'ASC')])
    seen = set()
    methods = []
    for line in lines:
        m = line.payment_method
        if m and m not in seen:
            seen.add(m)
            methods.append(m)
    return methods


class SalePaymentForm(metaclass=PoolMeta):
    __name__ = 'sale.payment.form'

    has_async_options = fields.Function(
        fields.Boolean("Async Options Available"),
        'on_change_with_has_async_options')

    @fields.depends('journal')
    def on_change_with_has_async_options(self, name=None):
        # Solo True si hay línea de config para este journal.
        return _journal_has_async_config(self.journal)


class AsyncMethodSelectForm(ModelView):
    "Async Payment Method Selection"
    __name__ = 'sale.async.method.select.form'

    journal = fields.Many2One(
        'account.statement.journal', "Journal",
        readonly=True,
        help="Journal used for the async payment (from the payment wizard).")
    payment_method = fields.Selection(
        'get_payment_method_selection', "Method", required=True, sort=False,
        states={'readonly': Eval('payment_method_readonly', False)},
        depends=['payment_method_readonly'],
        help="Async payment method. Available options depend on the "
             "journal.")
    payment_method_readonly = fields.Function(
        fields.Boolean("Method Fixed"),
        'on_change_with_payment_method_readonly')
    payment_amount = fields.Numeric(
        "Amount", digits=(16, 2), required=True)
    notes = fields.Text("Notes")

    @fields.depends('journal')
    def get_payment_method_selection(self):
        # Selection dinámica: solo los métodos que el administrador
        # habilitó en sale.async_payment.config.line para este journal.
        # El decorador @fields.depends provoca que selection_change_with
        # quede en {'journal'} → el cliente refresca opciones al cambiar
        # el journal y la RPC se invoca con instancia (instantiate=0).
        if not self.journal:
            return []
        labels = dict(PAYMENT_METHODS)
        return [(m, labels.get(m, m))
                for m in _async_methods_for_journal(self.journal)]

    @fields.depends('journal')
    def on_change_with_payment_method_readonly(self, name=None):
        methods = _async_methods_for_journal(self.journal)
        return len(methods) <= 1


class AsyncConfirmForm(ModelView):
    "Async Payment Registration Result"
    __name__ = 'sale.async.confirm.form'

    payment_method_label = fields.Char("Method", readonly=True)
    amount = fields.Numeric(
        "Registered Amount", digits=(16, 2), readonly=True)
    payment_url = fields.Char(
        "Payment URL", readonly=True,
        help="Share this link with the customer.")
    message = fields.Text("Message", readonly=True)


class WizardSalePayment(metaclass=PoolMeta):
    __name__ = 'sale.payment'

    # Tryton hace deepcopy de los StateView buscando el "último" en MRO,
    # lo que ignora cualquier redeclaración en una subclase. La forma
    # correcta de insertar botones nuevos en 'start' es modificar
    # start.buttons desde __setup__ después de llamar al super.

    async_method_select = StateView('sale.async.method.select.form',
        'sale_async_payment.async_method_select_view_form', [
            Button("Back", 'start', 'tryton-back'),
            Button("Register", 'async_register',
                'tryton-ok', default=True),
        ])

    async_register = StateTransition()

    async_confirm = StateView('sale.async.confirm.form',
        'sale_async_payment.async_confirm_view_form', [
            Button("Close", 'end', 'tryton-ok', default=True),
        ])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        # Insert "Async Payment" between Cancel and Pay buttons of the
        # base wizard's 'start' state.
        async_button = Button(
            "Async Payment", 'async_method_select', 'tryton-launch',
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
        methods = _async_methods_for_journal(journal)
        default_method = methods[0] if methods else None
        # Default sugerido: residual efectivo (total - pagado - async
        # pendiente), no el payment_amount tipeado en el form base.
        # Si la venta tiene async pending suma N y queda residual R,
        # ofrecemos R; cuando R<=0 ofrecemos lo que tipeó el usuario
        # como fallback (no debería llegar acá porque el botón es
        # invisible en ese caso).
        sale = self.record
        effective = (
            getattr(sale, 'effective_residual_amount', None)
            if sale is not None else None)
        if effective is not None and effective > 0:
            amount = effective
        else:
            amount = self.start.payment_amount or Decimal('0.0')
        return {
            'journal': journal.id if journal else None,
            'payment_method': default_method,
            'payment_method_readonly': len(methods) <= 1,
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
        # The async button only appears when the journal has a config
        # line. We re-validate here against post-render tampering.
        if not _journal_has_async_config(journal):
            raise UserError(
                "Journal '%s' has no async payments enabled. "
                "Configure it under Configuration → Async Payments."
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
            raise UserError("Amount must be greater than zero.")

        self._async_validate_journal(journal)

        method = form.payment_method
        allowed = _async_methods_for_journal(journal)
        if not method or method not in allowed:
            raise UserError(
                "Method '%s' is not enabled for journal '%s'. "
                "Valid options: %s."
                % (method or '-',
                   getattr(journal, 'name', '?'),
                   ', '.join(allowed) if allowed else '(none)'))

        shop_id = sale.shop.id if getattr(sale, 'shop', None) else None
        company_id = (
            sale.company.id if getattr(sale, 'company', None)
            else None)
        Config = pool.get('sale.async_payment.config')
        config = Config(1)
        expiration_date = config.compute_expiration_date(journal, method)
        vals = {
            'company': company_id,
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
            message = ("Async payment registered. Share the payment "
                "link with the customer. When confirmed, the system "
                "will process it automatically.")
        elif ap.payment_method == 'mp_link':
            message = ("Payment registered, but no link URL was "
                "obtained. Check the Mercado Pago configuration.")
        else:
            message = ("Async payment registered as Pending. It will "
                "be confirmed when the payment arrives.")
        return {
            'payment_method_label': label,
            'amount': ap.amount,
            'payment_url': url,
            'message': message,
        }
