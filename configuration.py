import datetime

from trytond.exceptions import UserError
from trytond.model import (
    ModelSingleton, ModelSQL, ModelView, Unique, fields, sequence_ordered)

from .async_payment import (
    ASYNC_CAPABLE_METHODS, ASYNC_CAPABLE_METHOD_LABELS)


EXPIRATION_UNIT = [
    ('hours', 'Horas'),
    ('days', 'Días'),
]


class AsyncPaymentConfig(ModelSingleton, ModelSQL, ModelView):
    "Configuración de cobros asíncronos"
    __name__ = 'sale.async_payment.config'

    default_expiration_value = fields.Integer(
        'Vencimiento por defecto',
        help='Cantidad de tiempo hasta que vence un cobro pendiente '
             'cuando no hay una regla específica para el diario.')
    default_expiration_unit = fields.Selection(
        EXPIRATION_UNIT, 'Unidad por defecto',
        help='Unidad del vencimiento por defecto: Horas o Días.')
    lines = fields.One2Many(
        'sale.async_payment.config.line', 'config',
        'Vencimiento por diario',
        help='Cada diario habilitado para cobros asíncronos debe '
             'figurar acá. Si una línea no define vencimiento, se usa '
             'el valor por defecto.')

    @staticmethod
    def default_default_expiration_value():
        return 48

    @staticmethod
    def default_default_expiration_unit():
        return 'hours'

    def compute_expiration_date(self, journal):
        # Busca la línea para el journal recibido; si no encuentra,
        # cae al default. journal es el record completo (o None).
        line = None
        if journal is not None:
            journal_id = getattr(journal, 'id', None)
            for l in (self.lines or []):
                line_journal = getattr(l, 'journal', None)
                if line_journal and getattr(
                        line_journal, 'id', None) == journal_id:
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
    "Diario habilitado para cobros asíncronos"
    __name__ = 'sale.async_payment.config.line'

    config = fields.Many2One(
        'sale.async_payment.config', 'Configuración',
        required=True, ondelete='CASCADE')
    journal = fields.Many2One(
        'account.statement.journal', 'Diario',
        required=True, ondelete='CASCADE',
        domain=_async_capable_domain(),
        help='Diario de extracto bancario habilitado para cobros '
             'asíncronos. Solo se listan diarios cuyo método de pago '
             'admite el flujo asíncrono.')
    journal_payment_method = fields.Function(
        fields.Char('Método del diario'),
        'on_change_with_journal_payment_method')
    expiration_value = fields.Integer(
        'Valor',
        help='Cantidad de horas o días hasta que vence el cobro. '
             'Si está vacío, usa el valor por defecto de la configuración.')
    expiration_unit = fields.Selection(
        [('', '')] + EXPIRATION_UNIT, 'Unidad',
        help='Horas o días. Si está vacío, usa la unidad por defecto.')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('journal_unique', Unique(t, t.journal),
                'Ya existe una línea para este diario.'),
        ]

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

    def check_journal_async_capable(self):
        # El domain del campo ya restringe la UI, pero validamos también
        # acá para resistir cambios via RPC, scripts o cambios al
        # payment_method del journal después de crear la línea.
        if not self.journal:
            return
        pm = getattr(self.journal, 'payment_method', None)
        if pm not in ASYNC_CAPABLE_METHODS:
            labels = ', '.join(
                ASYNC_CAPABLE_METHOD_LABELS.get(m, m)
                for m in ASYNC_CAPABLE_METHODS)
            raise UserError(
                "El diario '%s' (método '%s') no admite cobros "
                "asíncronos. Los métodos compatibles son: %s."
                % (self.journal.name, pm or 'none', labels))


class AsyncPaymentUserFilter(ModelSQL, ModelView):
    "Filtro de cobros asíncronos por usuario"
    __name__ = 'sale.async_payment.user_filter'

    user = fields.Many2One(
        'res.user', 'Usuario', required=True, ondelete='CASCADE')
    shops = fields.Many2Many(
        'sale.async_payment.user_filter-sale.shop',
        'user_filter', 'shop',
        'Sucursales visibles',
        help='Vacío significa todas las sucursales.')
    only_own = fields.Boolean(
        'Solo mis cobros',
        help='Si está activo, solo muestra cobros registrados por este usuario.')

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints = [
            ('user_unique', Unique(t, t.user),
                'Ya existe un filtro para este usuario.'),
        ]

    def get_rec_name(self, name):
        return self.user.rec_name if self.user else ''


class AsyncPaymentUserFilterShop(ModelSQL):
    "Filtro de usuario — Sucursales"
    __name__ = 'sale.async_payment.user_filter-sale.shop'

    user_filter = fields.Many2One(
        'sale.async_payment.user_filter', 'Filtro', required=True,
        ondelete='CASCADE')
    shop = fields.Many2One(
        'sale.shop', 'Sucursal', required=True, ondelete='CASCADE')
