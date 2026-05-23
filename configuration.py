import datetime

from trytond.exceptions import UserError
from trytond.model import (
    ModelSingleton, ModelSQL, ModelView, Unique, fields, sequence_ordered)
from trytond.pool import Pool
from trytond.pyson import Eval
from trytond.transaction import Transaction

from .async_payment import (
    ASYNC_CAPABLE_METHODS, ASYNC_CAPABLE_METHOD_LABELS,
    JOURNAL_ASYNC_METHODS, PAYMENT_METHODS)


EXPIRATION_UNIT = [
    ('hours', 'Hours'),
    ('days', 'Days'),
]


class AsyncPaymentConfig(ModelSingleton, ModelSQL, ModelView):
    "Async Payment Configuration"
    __name__ = 'sale.async_payment.config'

    default_expiration_value = fields.Integer(
        "Default Expiration",
        help="Default time before a pending payment expires when no "
             "journal-specific rule applies.")
    default_expiration_unit = fields.Selection(
        EXPIRATION_UNIT, "Default Unit",
        help="Default expiration unit: Hours or Days.")
    lines = fields.One2Many(
        'sale.async_payment.config.line', 'config',
        "Expiration per Journal",
        help="Each journal enabled for async payments must be listed "
             "here. Lines without an expiration value fall back to the "
             "default.")

    @staticmethod
    def default_default_expiration_value():
        return 48

    @staticmethod
    def default_default_expiration_unit():
        return 'hours'

    def compute_expiration_date(self, journal, payment_method=None):
        # Busca la línea para (journal, payment_method). Si no se pasa
        # payment_method (compat con callers viejos), toma la primera
        # línea del journal. Sin coincidencia → defaults.
        line = None
        if journal is not None:
            journal_id = getattr(journal, 'id', None)
            for l in (self.lines or []):
                line_journal = getattr(l, 'journal', None)
                if not line_journal or getattr(
                        line_journal, 'id', None) != journal_id:
                    continue
                if payment_method is None:
                    line = l
                    break
                if getattr(l, 'payment_method', None) == payment_method:
                    line = l
                    break
        if line is not None:
            value = line.expiration_value or self.default_expiration_value or 48
            unit = line.expiration_unit or self.default_expiration_unit or 'hours'
        else:
            value = self.default_expiration_value or 48
            unit = self.default_expiration_unit or 'hours'
        delta = (datetime.timedelta(days=value)
            if unit == 'days' else datetime.timedelta(hours=value))
        return datetime.datetime.now() + delta


def _async_capable_domain():
    # Reutilizable: domain para Many2One a journal limitado a métodos
    # de pago asincronizables.
    return [('payment_method', 'in', list(ASYNC_CAPABLE_METHODS))]


class AsyncPaymentConfigLine(ModelSQL, ModelView, sequence_ordered()):
    "Enabled Async Payment Method"
    __name__ = 'sale.async_payment.config.line'

    config = fields.Many2One(
        'sale.async_payment.config', "Configuration",
        required=True, ondelete='CASCADE')
    journal = fields.Many2One(
        'account.statement.journal', "Journal",
        required=True, ondelete='CASCADE',
        domain=_async_capable_domain(),
        help="Statement journal enabled for async payments. Only "
             "journals whose payment method supports the async flow "
             "are listed.")
    journal_payment_method = fields.Function(
        fields.Char("Journal Method"),
        'on_change_with_journal_payment_method')
    payment_method = fields.Selection(
        'get_payment_method_selection', "Async Method", required=True,
        sort=False,
        help="Async payment type enabled for this journal. Available "
             "options depend on the journal's payment method.")
    expiration_value = fields.Integer(
        "Value",
        help="Hours or days until the payment expires. If empty, the "
             "configuration default is used.")
    expiration_unit = fields.Selection(
        [('', '')] + EXPIRATION_UNIT, "Unit",
        help="Hours or days. If empty, the default unit is used.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('journal_payment_method_unique',
                Unique(t, t.journal, t.payment_method),
                "There is already a line for this journal and method."),
        ]

    @classmethod
    def __register__(cls, module_name):
        # Migración: dropear el viejo constraint journal_unique y
        # backfillear payment_method (= primer método válido del
        # journal según JOURNAL_ASYNC_METHODS) para filas previas.
        pool = Pool()
        Journal = pool.get('account.statement.journal')
        table_h = cls.__table_handler__(module_name)
        column_missing = not table_h.column_exist('payment_method')
        if 'journal_unique' in [
                c.removeprefix(table_h.table_name + '_')
                for c in table_h._constraints]:
            table_h.drop_constraint('journal_unique')
        super().__register__(module_name)
        if column_missing:
            cursor = Transaction().connection.cursor()
            t = cls.__table__()
            # Backfill: cada fila huérfana toma el primer método de
            # JOURNAL_ASYNC_METHODS para el payment_method de su journal.
            cursor.execute(*t.select(t.id, t.journal,
                where=(t.payment_method == None)))
            rows = list(cursor.fetchall())
            if rows:
                journal_ids = list({r[1] for r in rows})
                journals = {
                    j.id: j for j in Journal.browse(journal_ids)}
                for row_id, journal_id in rows:
                    j = journals.get(journal_id)
                    journal_pm = getattr(j, 'payment_method', None) if j else None
                    methods = JOURNAL_ASYNC_METHODS.get(journal_pm, [])
                    if not methods:
                        # Sin método válido: la fila no debería existir
                        # bajo el nuevo schema; la borramos.
                        cursor.execute(*t.delete(
                            where=(t.id == row_id)))
                        continue
                    cursor.execute(*t.update(
                        columns=[t.payment_method],
                        values=[methods[0]],
                        where=(t.id == row_id)))

    @classmethod
    def get_payment_method_selection(cls):
        # Selection global con todos los métodos posibles. El filtrado
        # por journal se hace en la validación (no en la UI), porque
        # Selection con domain dinámico es complejo en Tryton.
        return list(PAYMENT_METHODS)

    @staticmethod
    def default_expiration_unit():
        return ''

    @fields.depends('journal')
    def on_change_with_journal_payment_method(self, name=None):
        if self.journal:
            return getattr(self.journal, 'payment_method', None) or ''
        return ''

    @classmethod
    def validate(cls, records):
        super().validate(records)
        for record in records:
            record.check_journal_async_capable()
            record.check_payment_method_for_journal()

    def check_journal_async_capable(self):
        # The field domain already restricts the UI, but we also
        # validate here to resist RPC/script writes or post-creation
        # changes to journal.payment_method.
        if not self.journal:
            return
        pm = getattr(self.journal, 'payment_method', None)
        if pm not in ASYNC_CAPABLE_METHODS:
            labels = ', '.join(
                ASYNC_CAPABLE_METHOD_LABELS.get(m, m)
                for m in ASYNC_CAPABLE_METHODS)
            raise UserError(
                "Journal '%s' (method '%s') does not support async "
                "payments. Supported methods are: %s."
                % (self.journal.name, pm or 'none', labels))

    def check_payment_method_for_journal(self):
        # The chosen payment_method must be in the JOURNAL_ASYNC_METHODS
        # whitelist for the journal's payment_method.
        if not self.journal or not self.payment_method:
            return
        journal_pm = getattr(self.journal, 'payment_method', None)
        allowed = JOURNAL_ASYNC_METHODS.get(journal_pm, [])
        if self.payment_method not in allowed:
            label = dict(PAYMENT_METHODS).get(
                self.payment_method, self.payment_method)
            allowed_labels = ', '.join(
                dict(PAYMENT_METHODS).get(m, m) for m in allowed)
            raise UserError(
                "Method '%s' is not valid for journal '%s'. "
                "Valid options: %s."
                % (label, self.journal.name,
                   allowed_labels or '(none)'))


class AsyncPaymentUserFilter(ModelSQL, ModelView):
    "User Filter for Async Payments"
    __name__ = 'sale.async_payment.user_filter'

    company = fields.Many2One(
        'company.company', "Company", required=True, ondelete='RESTRICT')
    user = fields.Many2One(
        'res.user', "User", required=True, ondelete='CASCADE')
    shops = fields.Many2Many(
        'sale.async_payment.user_filter-sale.shop',
        'user_filter', 'shop',
        "Visible Shops",
        help="Empty means all shops.")
    only_own = fields.Boolean(
        "Only My Payments",
        help="If active, shows only payments registered by this user.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints = [
            ('user_company_unique', Unique(t, t.user, t.company),
                "There is already a filter for this user and company."),
        ]

    @classmethod
    def __register__(cls, module_name):
        # Drop el constraint user_unique anterior si quedó del schema viejo.
        table_h = cls.__table_handler__(module_name)
        if 'user_unique' in [
                c.removeprefix(table_h.table_name + '_')
                for c in table_h._constraints]:
            table_h.drop_constraint('user_unique')
        super().__register__(module_name)

    @staticmethod
    def default_company():
        return Transaction().context.get('company')

    def get_rec_name(self, name):
        return self.user.rec_name if self.user else ''


class AsyncPaymentUserFilterShop(ModelSQL):
    "User Filter — Shops"
    __name__ = 'sale.async_payment.user_filter-sale.shop'

    user_filter = fields.Many2One(
        'sale.async_payment.user_filter', "Filter", required=True,
        ondelete='CASCADE')
    shop = fields.Many2One(
        'sale.shop', "Shop", required=True, ondelete='CASCADE')
