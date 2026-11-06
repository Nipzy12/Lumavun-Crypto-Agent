"""Bounded, resumable finalized wallet-address history reconciliation."""
from datetime import datetime, timezone, timedelta
from decimal import InvalidOperation
import time
from wallet import _rpc_post
from source_security import check_source_destination


class BudgetExhausted(RuntimeError):
    pass


class HistoryGap(RuntimeError):
    pass


def reconcile(address, providers, tracker, *, now=None, rpc=None, sleep=time.sleep, page_size=20, max_pages=2, request_budget=24):
    from rewards import transaction_changes
    now = now or datetime.now(timezone.utc)
    rpc = rpc or _rpc_post
    previous = tracker.get('monitor')
    monitor = dict(previous) if isinstance(previous, dict) else {}
    monitor.setdefault('cursor', None)
    monitor.setdefault('pending', {})
    pending = monitor['pending']
    if not isinstance(pending, dict) or any(not isinstance(sig, str) or not isinstance(entry, dict) or 'err' not in entry for sig, entry in pending.items()):
        monitor.update(status='INVALID_STATE', updated_at=now.isoformat(), gaps=['MALFORMED_PENDING_HISTORY'], history_complete_to_cursor=False)
        tracker['monitor'] = monitor
        return [], [], ['Invalid monitoring state; cursor and historical wallet records preserved'], monitor
    monitor['pending'] = dict(pending)
    errors, events, completed = [], [], []
    try:
        retry_at = datetime.fromisoformat(monitor.get('retry_at') or '')
    except (ValueError, TypeError):
        retry_at = None
    if retry_at and retry_at.tzinfo and retry_at > now:
        monitor.update(status='BACKOFF', updated_at=now.isoformat())
        tracker['monitor'] = monitor
        return [], [], ['Transaction monitoring backoff; previous cursor preserved'], monitor
    endpoints = []
    for url in providers:
        if not url or url in endpoints:
            continue
        try:
            if check_source_destination(url):
                endpoints.append(url)
        except (ValueError, OSError):
            continue
    endpoints = endpoints[:2]
    calls = 0
    provider_used = None

    def call(method, params, validate):
        nonlocal calls, provider_used
        for attempt in range(max(1, len(endpoints)) * 2):
            if calls >= request_budget:
                raise BudgetExhausted('Request budget reached')
            if not endpoints:
                raise RuntimeError('No trusted endpoint available')
            index = attempt % len(endpoints)
            calls += 1
            try:
                if not check_source_destination(endpoints[index]):
                    raise ValueError("Unsafe RPC destination")
                body = rpc(endpoints[index], {'jsonrpc': '2.0', 'id': calls, 'method': method, 'params': params}, timeout=8)
                result = validate(body.get('result'))
                provider_used = index + 1
                return result
            except (RuntimeError, ValueError, TypeError, KeyError, InvalidOperation):
                if attempt + 1 < len(endpoints) * 2 and calls < request_budget:
                    sleep(min(2, .25 * 2 ** attempt))
        raise RuntimeError('RPC unavailable or invalid')

    def signature_rows(rows):
        if not isinstance(rows, list) or len(rows) > page_size:
            raise ValueError('Invalid signatures')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('signature'), str) or not row['signature'] or row['signature'] in seen or 'err' not in row or row.get('confirmationStatus') != 'finalized':
                raise ValueError('Invalid or unfinalized signature')
            seen.add(row['signature'])
        return rows

    empty = False
    try:
        for _ in range(max_pages):
            if monitor.get('boundary_complete') or monitor['pending']:
                break
            options = {'limit': page_size, 'commitment': 'finalized'}
            if monitor.get('before'):
                options['before'] = monitor['before']
            rows = call('getSignaturesForAddress', [address, options], signature_rows)
            empty = not rows and not monitor.get('head') and not monitor.get('cursor')
            if rows and not monitor.get('head'):
                monitor['head'] = rows[0]['signature']
            found_cursor = False
            for row in rows:
                sig = row['signature']
                if sig == monitor.get('cursor'):
                    found_cursor = True
                    break
                if sig not in tracker.get('signatures', {}):
                    monitor['pending'][sig] = {'err': row['err'], 'slot': row.get('slot')}
            if found_cursor or (len(rows) < page_size and not monitor.get('cursor')):
                monitor['boundary_complete'] = True
                break
            if len(rows) < page_size:
                raise HistoryGap('Previous cursor missing from available provider history')
            before = rows[-1]['signature']
            if before == monitor.get('before'):
                raise RuntimeError('Provider pagination did not advance')
            monitor['before'] = before
        for sig, entry in list(monitor['pending'].items()):
            if sig not in tracker.get('signatures', {}) and entry['err'] is None:
                def parse(tx):
                    if not isinstance(tx, dict) or type(tx.get('slot')) is not int or tx['slot'] < 0:
                        raise ValueError('Missing transaction slot')
                    if entry.get('slot') is not None and entry['slot'] != tx['slot']:
                        raise ValueError('Transaction slot mismatch')
                    meta = tx.get('meta')
                    if not isinstance(meta, dict) or any(not isinstance(meta.get(k), list) for k in ('preTokenBalances', 'postTokenBalances')):
                        raise ValueError('SPL balance evidence incomplete')
                    return transaction_changes(tx, address, sig)
                events.extend(call('getTransaction', [sig, {'encoding': 'jsonParsed', 'commitment': 'finalized', 'maxSupportedTransactionVersion': 0}], parse))
            completed.append(sig)
            del monitor['pending'][sig]
        complete = monitor.get('boundary_complete') and not monitor['pending']
        if complete:
            monitor['cursor'] = monitor.get('head') or monitor['cursor']
            for key in ('before', 'head', 'boundary_complete'):
                monitor.pop(key, None)
            monitor['last_success_at'] = now.isoformat()
        monitor.update(status='ZERO_TRANSFERS' if empty and complete else 'OK' if complete else 'PARTIAL', failures=0, retry_at=None,
                       history_complete_to_cursor=bool(complete), gaps=[] if complete else ['PAGINATION_PENDING'])
    except (BudgetExhausted, HistoryGap) as exc:
        gap = 'REQUEST_BUDGET_REACHED' if isinstance(exc, BudgetExhausted) else 'CURSOR_NOT_FOUND'
        monitor.update(status='PARTIAL', history_complete_to_cursor=False, gaps=[gap], retry_at=None)
        errors.append('Unresolved transaction history: ' + gap)
    except RuntimeError:
        failures = monitor.get('failures', 0)
        failures = failures if type(failures) is int and failures >= 0 else 0
        failures = min(failures + 1, 10)
        monitor.update(status='RPC_UNAVAILABLE', failures=failures, retry_at=(now + timedelta(seconds=min(3600, 5 * 2 ** failures))).isoformat(),
                       history_complete_to_cursor=False, gaps=['UNRESOLVED_HISTORY_OR_TRANSACTION'])
        errors.append('Transaction RPC unavailable, invalid, or history incomplete; cursor preserved')
    monitor.update(updated_at=now.isoformat(), requests=calls, provider_number=provider_used,
                   configured_provider_count=len(endpoints), scope='wallet-address signatures only; token-account-only history is not enumerated',
                   completeness='PARTIAL_SCOPE', scope_gaps=['TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED'])
    tracker['monitor'] = monitor
    return events, completed, errors, monitor
