import datetime

from trytond.model import (
    ModelSingleton, ModelSQL, ModelView, Unique, fields, sequence_ordered)

from .async_payment import PAYMENT_METHODS


EXPIRATION_UNIT = [
    ('hours', 'Horas'),
    ('days', 'Días'),
]

# Excluye 'other' — ese siempre usa el default
_METHODS_FOR_LINE = [(k, v) for k, v in PAYMENT_METHODS if k != 'other']


class AsyncPaymentConfig(ModelSingleton, ModelSQL, ModelView):
    "Configuración de cobros asíncronos"
    __name__ = 'sale.async_payment.config'

    default_expiration_value = fields.Integer(
        'Vencimiento por defecto',
        help='Cantidad de tiempo hasta que vence un cobro pendiente '
             'cuando no hay una regla específica para el método.')
    default_expiration_unit = fields.Selection(
        EXPIRATION_UNIT, 'Unidad por defecto',
        help='Unidad del vencimiento por defecto: Horas o Días.')
    lines = fields.One2Many(
        'sale.async_payment.config.line', 'config',
        'Vencimiento por método',
        help='Si el método coincide, usa ese vencimiento. '
             'Sin regla → se aplica el valor por defecto.')

    @staticmethod
    def default_default_expiration_value():
        return 48

    @staticmethod
    def default_default_expiration_unit():
        return 'hours'

    def compute_expiration_date(self, payment_method):
        for line in (self.lines or []):
            if line.payment_method == payment_method:
                value = line.expiration_value or self.default_expiration_value or 48
                unit = line.expiration_unit or self.default_expiration_unit or 'hours'
                break
        else:
            value = self.default_expiration_value or 48
            unit = self.default_expiration_unit or 'hours'
        delta = (datetime.timedelta(days=value)
            if unit == 'days' else datetime.timedelta(hours=value))
        return datetime.datetime.now() + delta


class AsyncPaymentConfigLine(ModelSQL, ModelView, sequence_ordered()):
    "Vencimiento por método de cobro"
    __name__ = 'sale.async_payment.config.line'

    config = fields.Many2One(
        'sale.async_payment.config', 'Configuración',
        required=True, ondelete='CASCADE')
    payment_method = fields.Selection(
        _METHODS_FOR_LINE, 'Método de cobro', required=True)
    expiration_value = fields.Integer(
        'Valor',
        help='Cantidad de horas o días hasta que vence el cobro.')
    expiration_unit = fields.Selection(
        EXPIRATION_UNIT, 'Unidad')

    @staticmethod
    def default_expiration_value():
        return 48

    @staticmethod
    def default_expiration_unit():
        return 'hours'


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
