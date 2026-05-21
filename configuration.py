from trytond.model import ModelSingleton, ModelSQL, ModelView, Unique, fields


class AsyncPaymentConfig(ModelSingleton, ModelSQL, ModelView):
    "Configuración de cobros asíncronos"
    __name__ = 'sale.async_payment.config'

    expiration_hours_mp_link = fields.Integer(
        'Horas de vencimiento — Link MP',
        help='Horas hasta que vence un cobro pendiente por Link Mercado Pago.')
    expiration_hours_bank_transfer = fields.Integer(
        'Horas de vencimiento — Transferencia',
        help='Horas hasta que vence un cobro pendiente por transferencia bancaria.')
    expiration_hours_debin = fields.Integer(
        'Horas de vencimiento — DEBIN',
        help='Horas hasta que vence un cobro pendiente por DEBIN.')
    expiration_hours_other = fields.Integer(
        'Horas de vencimiento — Otro',
        help='Horas hasta que vence un cobro pendiente por otros métodos.')

    @staticmethod
    def default_expiration_hours_mp_link():
        return 72

    @staticmethod
    def default_expiration_hours_bank_transfer():
        return 48

    @staticmethod
    def default_expiration_hours_debin():
        return 24

    @staticmethod
    def default_expiration_hours_other():
        return 48


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
