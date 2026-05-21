from trytond.model import fields
from trytond.pool import PoolMeta


class User(metaclass=PoolMeta):
    __name__ = 'res.user'

    async_payment_filters = fields.One2Many(
        'sale.async_payment.user_filter', 'user',
        'Filtros de cobros asíncronos',
        help='Configura qué cobros asíncronos ve este usuario en '
             'la vista Cobros pendientes.')
