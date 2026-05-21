from trytond.model import (
    DeactivableMixin, ModelSQL, ModelView, Workflow, fields)
from trytond.pool import Pool
from trytond.pyson import Eval, If


PAYMENT_METHODS = [
    ('mp_link', 'Link Mercado Pago'),
    ('mp_qr_static', 'QR Mercado Pago (estático)'),
    ('bank_transfer', 'Transferencia bancaria'),
    ('debin', 'DEBIN'),
    ('other', 'Otro'),
]

STATES = [
    ('pending', 'Pendiente'),
    ('suggested', 'Sugerido'),
    ('confirmed', 'Confirmado'),
    ('expired', 'Vencido'),
    ('cancelled', 'Cancelado'),
]

MATCH_CRITERIA = [
    ('mp_payment_id', 'ID de pago MP'),
    ('bank_reference', 'Referencia bancaria'),
    ('payer_cuit', 'CUIT del pagador'),
    ('amount_exact', 'Importe exacto'),
    ('manual', 'Vinculación manual'),
]


class AsyncPayment(Workflow, ModelSQL, ModelView):
    "Cobro asíncrono"
    __name__ = 'sale.async_payment'

    sale = fields.Many2One(
        'sale.sale', 'Venta', required=True, ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'pending'})
    amount = fields.Numeric(
        'Importe', digits=(16, 2), required=True,
        states={'readonly': Eval('state') != 'pending'})
    journal = fields.Many2One(
        'account.statement.journal', 'Diario de extracto', required=True,
        states={'readonly': Eval('state') != 'pending'})
    payment_method = fields.Selection(
        PAYMENT_METHODS, 'Método de cobro', required=True,
        states={'readonly': Eval('state') != 'pending'})
    shop = fields.Many2One(
        'sale.shop', 'Sucursal',
        states={'readonly': Eval('state') != 'pending'})
    notes = fields.Text(
        'Notas',
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    state = fields.Selection(
        STATES, 'Estado', readonly=True, required=True)
    expiration_date = fields.DateTime(
        'Fecha de vencimiento',
        states={'readonly': Eval('state') != 'pending'})

    # Datos del pago recibido (se completan al sugerir/confirmar)
    received_amount = fields.Numeric(
        'Importe recibido', digits=(16, 2),
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    mp_payment_id = fields.Char(
        'ID de pago MP',
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    bank_reference = fields.Char(
        'Referencia bancaria',
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    payer_name = fields.Char(
        'Nombre del pagador',
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    payer_cuit = fields.Char(
        'CUIT del pagador',
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    match_criteria = fields.Selection(
        MATCH_CRITERIA + [('', '')], 'Criterio de coincidencia', sort=False,
        states={'readonly': Eval('state').in_(['confirmed', 'cancelled'])})
    matched_date = fields.DateTime(
        'Fecha de coincidencia',
        states={'readonly': True})

    # Vínculo con transacciones externas
    mp_transaction = fields.Many2One(
        'account.payment.mp.transaction', 'Transacción MP',
        ondelete='SET NULL',
        states={'invisible': Eval('payment_method') != 'mp_link',
                'readonly': Eval('state').in_(['confirmed', 'cancelled'])})

    # Resultado de la confirmación
    statement_line = fields.Many2One(
        'account.statement.line', 'Línea de extracto',
        readonly=True, ondelete='SET NULL')
    confirmed_by = fields.Many2One(
        'res.user', 'Confirmado por', readonly=True)
    confirmed_date = fields.DateTime(
        'Fecha de confirmación', readonly=True)

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
        cls._buttons = {}

    @staticmethod
    def default_state():
        return 'pending'

    @staticmethod
    def default_match_criteria():
        return ''

    def get_rec_name(self, name):
        sale_name = self.sale.rec_name if self.sale else ''
        method = dict(PAYMENT_METHODS).get(self.payment_method, '')
        return f'{sale_name} — {method}'
