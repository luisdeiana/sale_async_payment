import datetime

from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction


class QRDetection(metaclass=PoolMeta):
    __name__ = 'account.payment.qr.detection'

    @classmethod
    def _candidates_from_write_args(cls, args):
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
    def _is_detection_ready_for_async(cls, det):
        # El estado 'confirmed' del detection ya implica que el cajero
        # validó el match. La statement.line puede o no estar creada
        # (dependiendo del flow QR), así que no la exigimos aquí.
        return det.state == 'confirmed'

    @classmethod
    def write(cls, *args):
        candidate_ids = cls._candidates_from_write_args(args)
        super().write(*args)
        if not candidate_ids:
            return

        dets = cls.browse(sorted(candidate_ids))
        relevant = [
            d for d in dets if cls._is_detection_ready_for_async(d)]
        if not relevant:
            return

        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')
        aps = AsyncPayment.search([
            ('qr_detection', 'in', [d.id for d in relevant]),
            ('state', 'in', ['pending', 'suggested']),
        ])
        if not aps:
            return

        det_by_id = {d.id: d for d in relevant}
        cls._auto_confirm_linked_async(aps, det_by_id)

    @classmethod
    def _auto_confirm_linked_async(cls, async_payments, det_by_id):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')

        now = datetime.datetime.now()
        user_id = Transaction().user

        for ap in async_payments:
            if not ap.qr_detection:
                continue
            det = det_by_id.get(ap.qr_detection.id)
            if not det:
                continue
            vals = {
                'state': 'confirmed',
                'received_amount': getattr(det, 'amount', None),
                'confirmed_by': user_id,
                'confirmed_date': now,
                'bank_reference': getattr(det, 'bank_reference', None),
                'payer_name': getattr(det, 'payer_name', None),
                'payer_cuit': getattr(det, 'payer_cuit', None),
                'match_criteria': 'bank_reference',
                'matched_date': now,
            }
            stmt_line = getattr(det, 'statement_line', None)
            if stmt_line:
                vals['statement_line'] = stmt_line.id
            AsyncPayment.write([ap], vals)
