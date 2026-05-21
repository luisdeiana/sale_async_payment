import datetime

from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction


class MPTransaction(metaclass=PoolMeta):
    __name__ = 'account.payment.mp.transaction'

    @classmethod
    def _candidates_from_write_args(cls, args):
        # Sólo nos interesan los writes que tocan state o statement_line.
        # El webhook hace 2 writes: primero state=approved, luego
        # statement_line=<id>. Filtrando aquí nos limitamos a esos
        # casos y a recarga manual desde el wizard.
        candidates = set()
        actions = iter(args)
        for records, values in zip(actions, actions):
            if not isinstance(values, dict):
                continue
            if 'state' in values or 'statement_line' in values:
                for record in records:
                    candidates.add(record.id)
        return candidates

    @classmethod
    def _is_transaction_ready_for_async(cls, txn):
        # Sólo disparamos cuando la transaction está plenamente lista:
        # approved Y con statement.line ya creada por el webhook.
        return (
            txn.state == 'approved'
            and bool(getattr(txn, 'statement_line', None)))

    @classmethod
    def write(cls, *args):
        candidate_ids = cls._candidates_from_write_args(args)
        super().write(*args)
        if not candidate_ids:
            return

        # Re-leer los records después del write para ver el estado final.
        txns = cls.browse(sorted(candidate_ids))
        relevant = [
            t for t in txns if cls._is_transaction_ready_for_async(t)]
        if not relevant:
            return

        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        aps = AsyncPayment.search([
            ('mp_transaction', 'in', [t.id for t in relevant]),
            ('state', 'in', ['pending', 'suggested']),
        ])
        if not aps:
            return

        txn_by_id = {t.id: t for t in relevant}
        cls._auto_confirm_linked_async(aps, txn_by_id)

    @classmethod
    def _auto_confirm_linked_async(cls, async_payments, txn_by_id):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        StatementLine = pool.get('account.statement.line')

        now = datetime.datetime.now()
        user_id = Transaction().user

        # Vincular sale en la statement.line del webhook si está ausente.
        # El webhook MP actual NO setea sale al crear la línea (solo
        # invoice/party), por lo que paid_amount de la venta no sube
        # hasta que el bridge lo arregla.
        line_updates = []
        for ap in async_payments:
            if not ap.mp_transaction:
                continue
            txn = txn_by_id.get(ap.mp_transaction.id)
            if not txn or not txn.statement_line:
                continue
            line = txn.statement_line
            if not line.sale:
                line_updates.append((line, ap.sale.id))
        for line, sale_id in line_updates:
            StatementLine.write([line], {'sale': sale_id})

        # Auto-confirmar cada async. Write directo del state pasa por
        # Workflow.validate (pending→confirmed y suggested→confirmed
        # están en _transitions) sin disparar la creación de una nueva
        # statement.line vía confirm().
        for ap in async_payments:
            if not ap.mp_transaction:
                continue
            txn = txn_by_id.get(ap.mp_transaction.id)
            if not txn:
                continue
            vals = {
                'state': 'confirmed',
                'statement_line': (
                    txn.statement_line.id if txn.statement_line else None),
                'received_amount': txn.amount,
                'confirmed_by': user_id,
                'confirmed_date': now,
                'match_criteria': 'mp_payment_id',
                'matched_date': now,
            }
            AsyncPayment.write([ap], vals)
