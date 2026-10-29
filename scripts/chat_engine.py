"""Single local data pipeline. Python >=3.10, standard library only."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = '2.0.0'
MAX_BYTES = 32 * 1024 * 1024
MAX_MESSAGES = 100000
TIME_KEYS = ('time', 'timestamp', 'ts', 'date', 'datetime', 'createtime', 'create_time', 'msgcreatetime',
             '时间', '日期', '发送时间', '消息时间', '时间戳')
SENDER_KEYS = ('sender', 'from', 'speaker', 'name', 'nickname', 'talker', 'user', 'who', 'role',
               '发送者', '发送人', '发言人', '昵称', '姓名', '说话人')
TEXT_KEYS = ('text', 'content', 'msg', 'message', 'body', 'strcontent', 'content_text',
             '内容', '消息', '消息内容', '正文', '聊天内容')
TYPE_KEYS = ('type', 'msgtype', 'msg_type', 'kind', '类型', '消息类型')
# 内容列为空时，用类型列推断媒体占位文本（微信/TraceMemo 导出常见）
TYPE_PLACEHOLDER = {
    '图片': '[图片]', '照片': '[图片]', 'image': '[图片]',
    '视频': '[视频]', '小视频': '[视频]', 'voice': '[语音]', '语音': '[语音]',
    '动画表情': '[动画表情]', '表情': '[动画表情]', 'sticker': '[动画表情]',
    '文件': '[文件]', '链接': '[链接]', '分享': '[链接]', '名片': '[名片]',
    '位置': '[位置]', '转账': '[转账]', '红包': '[红包]',
    '聊天记录': '[聊天记录]', '合并转发': '[聊天记录]',
    '通话': '[语音通话]', '语音通话': '[语音通话]', '视频通话': '[视频通话]',
    '系统消息': '[系统消息]', '撤回': '[撤回]',
}
MEDIA = re.compile(r'\[(?:图片|照片|语音|视频|动画表情|表情|文件|链接|名片|聊天记录|位置|转账|红包|语音通话|视频通话)\]')
TIME_PATTERN = (r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?[ T]+'
                r'(?:(?:凌晨|上午|早上|中午|下午|晚上)\s*)?\d{1,2}:\d{2}(?::\d{2})?')
TIME_FIRST = re.compile(r'^\s*\[?(' + TIME_PATTERN + r')\]?\s*[-—]?\s*(.+?)\s*$')
NAME_FIRST = re.compile(r'^\s*(.+?)\s+\[?(' + TIME_PATTERN + r')\]?\s*$')
NAME_TEXT = re.compile(r'^([^:：\n]{1,80})[:：](.*)$')
DATE_START = re.compile(r'^\s*\[?\d{4}[-/年]\d{1,2}[-/月]')
SHORT_ACK = {'好', '好的', '好呀', '好啊', '可以', '行', '收到', '嗯', '嗯嗯', 'ok', '都行', '你定'}
POSITIVE = ('喜欢你', '爱你', '想你', '谢谢你', '感谢你', '谢谢', '感谢', '很开心', '好开心', '期待', '辛苦了', '真棒')
NEGATIVE = ('讨厌', '失望', '生气', '不喜欢', '不爱', '不想', '别联系', '不要联系', '不方便', '不愿意')
PRESSURE = ('压力', '焦虑', '难受', '难过', '失眠', '好累', '累了', '疲惫', '不舒服')
BOUNDARY = ('不要联系', '别联系', '不愿意', '不同意', '不方便', '不想去', '请停止', '别再')


class InputError(ValueError):
    pass


def alias(record, keys):
    vals = [v for k, v in record.items() if str(k).lower() in keys and v is not None and v != '']
    if any(v != vals[0] for v in vals[1:]):
        raise InputError('同一记录字段别名值冲突，请保留一个明确字段')
    return vals[0] if vals else None


def parse_time(value):
    """Return datetime or None. Never infer a date or timezone."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('boolean timestamp')
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r'-?\d{10,13}(?:\.\d+)?', value.strip())):
        n = float(value)
        if not math.isfinite(n):
            raise ValueError('non-finite timestamp')
        if abs(n) >= 1e11:
            n /= 1000
        return datetime.fromtimestamp(n, timezone.utc)
    if not isinstance(value, str):
        raise ValueError('invalid timestamp type')
    s = value.strip().replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
    period = re.search(r'(凌晨|上午|早上|中午|下午|晚上)', s)
    if period:
        p = period.group()
        s = s.replace(p, '').strip()
        d = datetime.strptime(s, '%Y-%m-%d %H:%M:%S' if s.count(':') == 2 else '%Y-%m-%d %H:%M')
        if p in ('下午', '晚上', '中午') and d.hour < 12:
            d += timedelta(hours=12)
        elif p in ('凌晨', '上午', '早上') and d.hour == 12:
            d -= timedelta(hours=12)
        return d
    # Date-only exports have insufficient precision for response latency.
    if not re.search(r'[ T]\d{1,2}:\d{2}', s):
        raise ValueError('timestamp lacks clock time')
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        d = datetime.strptime(s.replace('T', ' '), '%Y-%m-%d %H:%M:%S' if s.count(':') == 2 else '%Y-%m-%d %H:%M')
    return d.astimezone(timezone.utc) if d.tzinfo is not None else d


def decode(raw):
    encodings = ('utf-16',) if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            value = raw.decode(encoding)
            if '\x00' in value:
                raise InputError('输入含 NUL 字符或无 BOM 的 UTF-16，请导出 UTF-8')
            return value, encoding
        except UnicodeDecodeError:
            continue
    raise InputError('无法解码，请使用 UTF-8 或带 BOM 的 UTF-16')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError('JSON 含重复字段')
        result[key] = value
    return result


def load_upload(path):
    path = Path(path)
    if not path.is_file():
        raise InputError('上传路径不是可读取文件')
    if path.suffix.lower() not in ('.txt', '.json', '.csv'):
        raise InputError('只支持上传的 TXT/JSON/CSV，截图请先转写')
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError('输入超过 32 MiB，请按连续时间段拆分')
    content, encoding = decode(raw)
    sha = hashlib.sha256(raw).hexdigest()
    messages, warnings, unassigned = [], [], []

    def add(sender, text, time_value, locator):
        if not isinstance(sender, str) or not sender.strip():
            raise InputError(f'{locator} 缺少有效发送者')
        if text is None:
            text = ''
            warnings.append({'code': 'missing_text', 'source': locator})
        if not isinstance(text, str):
            raise InputError(f'{locator} 正文必须是字符串')
        if re.search(r'[\ud800-\udfff]', text+sender):
            raise InputError(f'{locator} 含无效 Unicode 字符')
        if len(messages) >= MAX_MESSAGES:
            raise InputError('消息超过 100000 条，请按连续时间段拆分')
        try:
            dt = parse_time(time_value)
        except (ValueError, TypeError, OverflowError, OSError):
            dt = None
            warnings.append({'code': 'invalid_time', 'source': locator})
        messages.append({'id': f'm{len(messages)+1:06d}', 'sender': sender.strip(), 'text': text,
                         'timestamp': dt.isoformat(sep=' ') if dt else None,
                         'timestamp_raw': time_value,
                         '_dt': dt, 'source': {'file': path.name, 'sha256': sha, **locator}})

    if path.suffix.lower() == '.txt':
        cur = None
        for number, line in enumerate(content.splitlines(), 1):
            if not line.strip():
                if cur is not None:
                    cur['text'] += '\n'
                    cur['source']['line_end'] = number
                continue
            hit = TIME_FIRST.match(line)
            name_hit = NAME_FIRST.match(line) if not hit else None
            timestamp, tail = (hit.group(1), hit.group(2)) if hit else ((name_hit.group(2), name_hit.group(1)) if name_hit else (None, line.strip()))
            parts = NAME_TEXT.match(tail)
            if hit or name_hit:
                sender, text = (parts.group(1), parts.group(2)) if parts else (tail.strip(), '')
                add(sender, text.strip(), timestamp, {'line_start': number, 'line_end': number})
                cur = messages[-1]
            elif DATE_START.match(line):
                unassigned.append({'line': number, 'text': line, 'reason': '无法识别的时间头'})
                cur = None
            elif parts:
                add(parts.group(1), parts.group(2).strip(), None, {'line_start': number, 'line_end': number})
                cur = messages[-1]
                warnings.append({'code': 'untimed_header_review', 'source': {'line': number}})
            elif cur is not None:
                cur['text'] = (cur['text'] + '\n' + line).lstrip('\n')
                cur['source']['line_end'] = number
            else:
                unassigned.append({'line': number, 'text': line, 'reason': '无发送者归属'})
        for m in messages:
            m['text'] = m['text'].rstrip('\n')
    else:
        if path.suffix.lower() == '.json':
            try:
                obj = json.loads(content, object_pairs_hook=unique_object)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise InputError('JSON 语法无效或嵌套过深') from exc
            if isinstance(obj, dict):
                candidates = [obj[k] for k in ('messages', 'records', 'data', 'list') if k in obj and isinstance(obj[k], list)]
                if len(candidates) != 1:
                    raise InputError('JSON 顶层须有唯一的消息数组')
                obj = candidates[0]
            if not isinstance(obj, list):
                raise InputError('JSON 必须是消息对象数组')
            rows = [(r, {'record': i}) for i, r in enumerate(obj, 1)]
        else:
            reader = csv.DictReader(io.StringIO(content), strict=True)
            names = reader.fieldnames or []
            if not names or len({n.casefold() for n in names}) != len(names):
                raise InputError('CSV 表头为空或重复')
            rows = []
            last_line = 1
            try:
                for i, r in enumerate(reader, 1):
                    if None in r or any(v is None for v in r.values()):
                        raise InputError(f'CSV 记录 {i} 列数与表头不一致')
                    rows.append((r, {'record': i, 'line_start': last_line+1, 'line_end': reader.line_num}))
                    last_line = reader.line_num
            except csv.Error as exc:
                raise InputError('CSV 语法无效') from exc
        for record, locator in rows:
            if not isinstance(record, dict):
                raise InputError(f'{locator} 必须是对象')
            text = alias(record, TEXT_KEYS)
            if not (text or '').strip():
                # 内容为空（图片/语音/通话等媒体行）→ 用类型列补占位文本，避免整行被判为无效
                t = alias(record, TYPE_KEYS)
                t = str(t).strip() if t else ''
                if t and t != '普通文本':
                    text = TYPE_PLACEHOLDER.get(t, f'[{t}]')
            add(alias(record, SENDER_KEYS), text or '', alias(record, TIME_KEYS), locator)
    if not messages:
        raise InputError('未解析到消息；请提供含发送者的聊天记录')
    for m in messages:
        if not m['text'].strip():
            warnings.append({'code': 'empty_text', 'message_id': m['id']})
    missing = sum(m['_dt'] is None for m in messages)
    zones = {m['_dt'].tzinfo is not None for m in messages if m['_dt'] is not None}
    time_ok = missing == 0 and len(zones) == 1 and not unassigned
    reasons = []
    if missing:
        reasons.append(f'{missing} 条缺有效时间，保留文件顺序')
    if len(zones) > 1:
        reasons.append('混合有时区和无时区时间')
    if unassigned:
        reasons.append(f'{len(unassigned)} 行无法归属')
    if time_ok:
        original = [m['id'] for m in messages]
        messages.sort(key=lambda m: m['_dt'])
        if original != [m['id'] for m in messages]:
            warnings.append({'code': 'sorted_by_timestamp'})
        if len({m['_dt'] for m in messages}) < len(messages):
            warnings.append({'code': 'tied_timestamps', 'note': '同时间戳保留文件顺序，不能推断同秒内因果'})
    return messages, {'file': path.name, 'sha256': sha, 'encoding': encoding, 'bytes': len(raw),
                      'input_messages': len(messages), 'warnings': warnings, 'unassigned': unassigned,
                      'missing_timestamps': missing, 'time_usable': time_ok, 'time_reasons': reasons}


def cues(text):
    plain = MEDIA.sub('', text).strip()
    compact = re.sub(r'[\s，。！？,.!?~～]+', '', plain).lower()
    # Deliberately conservative: ambiguous syntax goes to reading, not a score.
    ambiguous = bool(re.search(r'[不没无别未莫]|[“”「」『』"\']|但是|不过|并非|开玩笑|才怪', plain))
    hits = {k: [w for w in words if w in plain] for k, words in
            [('positive', POSITIVE), ('negative', NEGATIVE), ('pressure', PRESSURE), ('boundary', BOUNDARY)]}
    flags = {k: bool(v) and not ambiguous for k, v in hits.items()}
    return {'media': bool(MEDIA.search(text)), 'text_chars': len(plain),
            'short_ack': compact in SHORT_ACK,
            'question_candidate': bool(re.search(r'[?？]|(?:吗|么|呢)[。！!\s]*$', plain)),
            'lexical_hits': hits, 'manual_review': ambiguous or sum(bool(x) for x in hits.values()) > 1,
            **{k+'_candidate': v for k, v in flags.items()}}


def median(values):
    return round(statistics.median(values), 3) if values else None


def safe_ratio(recent, baseline):
    return None if recent is None or baseline in (None, 0) else round(recent/baseline, 4)


def metrics(messages, names, timed, gap_seconds):
    per = {}
    for name in names:
        mine = [m for m in messages if m['sender'] == name]
        cs = [m['_cues'] for m in mine]
        n = len(mine)
        lengths = [c['text_chars'] for c in cs]
        per[name] = {'message_count': n, 'text_chars_total': sum(lengths),
                     'text_chars_mean': round(statistics.mean(lengths), 3) if n else None,
                     'media_messages': sum(c['media'] for c in cs),
                     'short_ack_messages': sum(c['short_ack'] for c in cs),
                     'question_candidate_messages': sum(c['question_candidate'] for c in cs),
                     'question_candidate_ratio': round(sum(c['question_candidate'] for c in cs)/n, 4) if n else None,
                     'positive_candidate_messages': sum(c['positive_candidate'] for c in cs),
                     'negative_candidate_messages': sum(c['negative_candidate'] for c in cs),
                     'manual_review_messages': sum(c['manual_review'] for c in cs),
                     'reply_intervals_sec': [], 'reply_interval_median_sec': None,
                     'initiated_sessions': None, 'initiative_share': None}
    session_count = None
    if timed and messages:
        session_count = 0
        for name in names:
            per[name]['initiated_sessions'] = 0
        prev = None
        for m in messages:
            interval = (m['_dt']-prev['_dt']).total_seconds() if prev else None
            if prev is None or interval > gap_seconds:
                session_count += 1
                per[m['sender']]['initiated_sessions'] += 1
            elif m['sender'] != prev['sender']:
                per[m['sender']]['reply_intervals_sec'].append(interval)
            prev = m
        for name in names:
            p = per[name]
            p['reply_interval_median_sec'] = median(p['reply_intervals_sec'])
            p['initiative_share'] = round(p['initiated_sessions']/session_count, 4)
    return {'per_person': per, 'session_count': session_count, 'gap_seconds': gap_seconds,
            'time_metrics_available': timed, 'reply_interval_note': '同会话换人发言间隔，仅为代理指标，不是阅读或意愿度量'}


def window_summary(by_day, names, start, end, gap):
    subset = []
    day = start
    while day < end:
        subset.extend(by_day.get(day, []))
        day += timedelta(days=1)
    return {'start_inclusive': start.isoformat(), 'end_exclusive': end.isoformat(),
            'observed_days': len({m['_dt'].date() for m in subset}),
            'message_count': len(subset), 'message_ids': [m['id'] for m in subset],
            'metrics': metrics(subset, names, True, gap)}


def analyze(path, self_name, other=None, gap_minutes=120, split=0.67, time_unreliable=False):
    if not isinstance(self_name, str) or not self_name.strip():
        raise InputError('必须指定本人昵称')
    self_name = self_name.strip()
    other = other.strip() if isinstance(other, str) else other
    if not math.isfinite(gap_minutes) or gap_minutes <= 0:
        raise InputError('会话间隔必须是正数')
    if not math.isfinite(split) or not 0 < split < 1:
        raise InputError('split 必须在 0 与 1 之间')
    messages, source = load_upload(path)
    speakers = list(dict.fromkeys(m['sender'] for m in messages))
    if self_name not in speakers:
        raise InputError('本人昵称不在上传记录中，请按发言者原名指定')
    if other == self_name:
        raise InputError('本人和对方不能相同')
    if other and other not in speakers:
        raise InputError('对方昵称不在上传记录中')
    if not other:
        choices = [n for n in speakers if n != self_name]
        if len(choices) > 1:
            raise InputError('多人记录必须明确 --other，不能自动挑选两位')
        other = choices[0] if choices else None
    names = [self_name] + ([other] if other else [])
    selected = [m for m in messages if m['sender'] in names]
    excluded = len(messages)-len(selected)
    reasons = list(source['time_reasons'])
    if time_unreliable:
        reasons.append('用户标记时间精度不可靠')
    if excluded:
        reasons.append('含第三方发言，停用双人时序推断')
    if other is None:
        reasons.append('只有一位发言者，停用关系时序指标')
    timed = source['time_usable'] and not time_unreliable and excluded == 0 and other is not None
    if not timed:
        selected.sort(key=lambda m: m['id'])
    for m in selected:
        m['_cues'] = cues(m['text'])
    gap = gap_minutes*60
    overall = metrics(selected, names, timed, gap)
    span = (selected[-1]['_dt']-selected[0]['_dt']).total_seconds()/86400 if timed else None
    incomplete = bool(source['unassigned']) or any(w['code'] in ('empty_text', 'missing_text') for w in source['warnings'])
    mode = 'FULL' if timed and not incomplete and len(selected) >= 30 and span >= 3 else 'SNAPSHOT'
    timeline = [{'message_id': m['id'], 'sender': m['sender'], 'timestamp': m['timestamp'], **m['_cues']} for m in selected]
    expressions = {name: {k: [m['id'] for m in selected if m['sender'] == name and m['_cues'][k]]
                          for k in ('positive_candidate', 'negative_candidate', 'manual_review')}
                   for name in names}
    daily = []
    events = {'available': timed, 'reason': '; '.join(reasons) if not timed else None, 'items': []}
    drift = {'available': False, 'reason': '时间或双方样本不足', 'baseline': None, 'recent': None, 'changes': {}}
    if timed:
        by_day = {}
        for m in selected:
            by_day.setdefault(m['_dt'].date(), []).append(m)
        daily = [{'date': day.isoformat(), 'message_ids': [m['id'] for m in rows],
                  'metrics': metrics(rows, names, True, gap)} for day, rows in sorted(by_day.items())]
        # Every candidate is retained by day; no top-N truncation of evidence.
        candidates = {}
        for m in selected:
            h = m['_cues']['lexical_hits']
            if h['boundary'] or h['negative'] or h['pressure']:
                candidates.setdefault(m['_dt'].date(), []).append(m['id'])
        for day, ids in sorted(candidates.items()):
            events['items'].append({'date': day.isoformat(), 'candidate_message_ids': ids,
                                    'needs_review': True, 'interpretation': None,
                                    'baseline': window_summary(by_day, names, day-timedelta(days=7), day, gap),
                                    'event_window': window_summary(by_day, names, day, day+timedelta(days=2), gap),
                                    'following': window_summary(by_day, names, day+timedelta(days=2), day+timedelta(days=5), gap)})
        cut = selected[0]['_dt']+(selected[-1]['_dt']-selected[0]['_dt'])*split
        before = [m for m in selected if m['_dt'] < cut]
        after = [m for m in selected if m['_dt'] >= cut]
        b, r = metrics(before, names, True, gap), metrics(after, names, True, gap)
        sufficient = all(sum(m['sender'] == n for m in rows) >= 3 for n in names for rows in (before, after))
        drift = {'available': sufficient, 'reason': None if sufficient else '基线或近期有参与者不足 3 条消息',
                 'cut': cut.isoformat(sep=' '), 'baseline': b, 'recent': r, 'changes': {}}
        if sufficient:
            for name in names:
                change = {}
                for key in ('text_chars_mean', 'question_candidate_ratio', 'reply_interval_median_sec', 'initiative_share'):
                    bv, rv = b['per_person'][name][key], r['per_person'][name][key]
                    change[key] = {'baseline': bv, 'recent': rv,
                                   'difference': round(rv-bv, 4) if bv is not None and rv is not None else None,
                                   'ratio': safe_ratio(rv, bv)}
                drift['changes'][name] = change
    review = ['词汇候选须回原文复核，不是情绪、人格或因果结论。',
              '相同数据的多个指标不能作为独立证据，未观测日期不能视为零互动。']
    if incomplete:
        review.append('输入存在未归属行或缺正文，不能给完整关系结论。')
    if excluded:
        review.append('选定双方的群聊消息可能回应第三人，不能按私聊解释。')
    return {'meta': {'schema_version': '2.0', 'engine_version': VERSION,
                     'generated_at': datetime.now(timezone.utc).isoformat(),
                     'input': {k: source[k] for k in ('file', 'sha256', 'encoding', 'bytes')},
                     'parse': source, 'self': self_name, 'other': other, 'speakers': speakers,
                     'selected_messages': len(selected), 'excluded_messages': excluded,
                     'time_metrics_available': timed, 'time_unavailable_reasons': reasons,
                     'span_days': round(span, 4) if span is not None else None, 'mode': mode,
                     'parameters': {'gap_minutes': gap_minutes, 'split': split, 'time_unreliable': time_unreliable}},
            'messages': [{k: v for k, v in m.items() if not k.startswith('_')} for m in selected],
            'metrics': overall, 'timeline': {'messages': timeline, 'observed_days': daily},
            'events': events, 'expressions': expressions, 'drift': drift, 'review': review}


def escape_markdown(value):
    text = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()#+.!|~-])', r'\\\1', text).replace('\n', ' ').replace('\r', ' ')


def to_markdown(result):
    meta = result['meta']
    out = ['# 聊天记录统计', '', f"- 模式：{meta['mode']}",
           f"- 输入：{escape_markdown(meta['input']['file'])}",
           f"- 输入 SHA256：{meta['input']['sha256']}",
           f"- 选定消息：{meta['selected_messages']}；排除第三方：{meta['excluded_messages']}",
           f"- 时间指标可用：{meta['time_metrics_available']}", '', '## 参与者统计', '',
           '| 参与者 | 消息数 | 平均文字长度 | 疑问候选条数 | 媒体条数 | 回复间隔中位秒 |',
           '|---|---:|---:|---:|---:|---:|']
    for name, p in result['metrics']['per_person'].items():
        out.append('| ' + ' | '.join(escape_markdown(x) if x is not None else '不可用' for x in
                   (name, p['message_count'], p['text_chars_mean'], p['question_candidate_messages'],
                    p['media_messages'], p['reply_interval_median_sec'])) + ' |')
    out += ['', '## 数据边界', '']
    out += ['- '+escape_markdown(r) for r in meta['time_unavailable_reasons']+result['review']]
    out.append(f"- 解析告警 {len(meta['parse']['warnings'])} 项；未归属 {len(meta['parse']['unassigned'])} 行，详见 JSON。")
    out += ['', '## 后续阅读', '', '逐句读取 analysis_all.json 的 messages 并按 source 回原文核对，再撰写分析。',
            '词汇候选未经人工确认；本文件没有自动判断心态、人格或关系阶段。', '']
    return '\n'.join(out)
