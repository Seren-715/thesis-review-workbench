"""Share source-bound reading, never share a rule's final judgment.

Each shared read explicitly includes every member's teacher requirements and
dimensions. Existing single-rule notes cannot be relabeled as joint reading.
"""
from copy import deepcopy
import json

from content_context_agent import (ContextReviewQueue, CONTENT_POLICY, SYSTEM,
    MAX_BATCH_CHARACTERS, MAX_BATCH_EVIDENCE, _chunks, _dimensions, _evidence,
    _hash, _valid_note, _canonical_response, content_context_key)

SHARED_POLICY = 'joint_teacher_scope_read_v1'


def shared_scope_identity(f, source_sha256):
    scope = f['content_scope']
    chunks = _chunks(f)
    if scope.get('source_sha256') != source_sha256 or not scope.get('complete'):
        raise ValueError('联合阅读必须绑定同一来源和完整范围。')
    return _hash(dict(policy=SHARED_POLICY, content_policy=CONTENT_POLICY,
        source_sha256=source_sha256,
        chunks=[{k:c.get(k) for k in ('paragraph_index','start','end','text','role','location')} for c in chunks],
        headings=scope.get('headings'), coverage_basis=scope.get('coverage_basis'),
        unread_object_types=scope.get('unread_object_types')))


class SharedScopeContextReviewQueue(ContextReviewQueue):
    """Drop-in queue with joint reading followed by separate rule synthesis."""
    def __init__(self, report, state, output, allowed_rule_ids=None):
        super().__init__(report, state, output, allowed_rule_ids)
        self.groups = {}
        # Include already validated member decisions so a continuation does not
        # change the joint contract merely because one rule finished earlier.
        for f in report['findings']:
            if (not f.get('content_scope') or f.get('status') != 'candidate' or f.get('review_blocked') or
                allowed_rule_ids is not None and f.get('rule_id') not in allowed_rule_ids):
                continue
            try:
                sid = shared_scope_identity(f, report['source_sha256'])
            except ValueError:
                continue
            self.groups.setdefault(sid, []).append(f)
        self.groups = {sid: fs for sid,fs in self.groups.items() if len(fs)>1}
        self.by_finding = {f['id']:sid for sid,fs in self.groups.items() for f in fs}
        self.cache = report.setdefault('shared_content_reads', {})
        for sid, fs in self.groups.items():
            key = self._group_key(sid, fs)
            before = self.cache.get(sid, {})
            if not self._validate_cache(before, sid, fs, key):
                before = dict(policy=SHARED_POLICY, group_key=key,
                    source_sha256=report['source_sha256'], leader_id=fs[0]['id'], batches=[])
                self.cache[sid] = before
            self._apply(sid)

    def _group_key(self, sid, fs):
        return _hash(dict(scope=sid, members=[dict(id=f['id'],
            context_key=content_context_key(f, self.rules.get(f['rule_id'], {}))) for f in fs]))

    def _validate_cache(self, cache, sid, fs, key):
        if (not isinstance(cache,dict) or cache.get('policy')!=SHARED_POLICY or
            cache.get('group_key')!=key or cache.get('source_sha256')!=self.report['source_sha256'] or
            cache.get('leader_id')!=fs[0]['id'] or not isinstance(cache.get('batches'),list)):
            return False
        leader, seen = fs[0], set()
        chunks = {c['id']:c for c in _chunks(leader)}
        synthetic = dict(check_dimensions=self._all_dimensions(fs))
        for b in cache['batches']:
            if not isinstance(b,dict) or set(b)!={'batch_id','chunk_ids','notes'}:
                return False
            ids=b['chunk_ids']
            if not isinstance(ids,list) or not ids or any(i not in chunks or i in seen for i in ids) or len(set(ids))!=len(ids):
                return False
            expected='S'+_hash(dict(group_key=key,chunk_ids=ids))[:32]
            ev={e['evidence_id']:e for e in _evidence([chunks[i] for i in ids])}
            if (b['batch_id']!=expected or not isinstance(b['notes'],list) or
                len(b['notes'])>2*len(synthetic['check_dimensions']) or
                not all(_valid_note(n,synthetic,ev) for n in b['notes'])):
                return False
            seen.update(ids)
        return True

    def _all_dimensions(self, fs):
        return list(dict.fromkeys(d for f in fs for d in _dimensions(f)))

    def _apply(self, sid):
        fs=self.groups[sid]; leader=fs[0]; cache=self.cache[sid]
        # The cache was validated against exact chunks and teacher contracts.
        read={i for b in cache['batches'] for i in b['chunk_ids']}
        leader_chunks=_chunks(leader)
        leader_ev=_evidence(leader_chunks)
        for f in fs:
            target_chunks=_chunks(f)
            chunkmap={a['id']:b['id'] for a,b in zip(leader_chunks,target_chunks)}
            evmap={a['evidence_id']:b['evidence_id'] for a,b in zip(leader_ev,_evidence(target_chunks))}
            notes=[dict(n,evidence_ids=[evmap[e] for e in n['evidence_ids']])
                for batch in cache['batches'] for n in batch['notes'] if n['dimension'] in _dimensions(f)]
            f['content_read_progress']=dict(scope_context_key=content_context_key(f,self.rules.get(f['rule_id'],{})),
                read_chunk_ids=[chunkmap[c['id']] for c in leader_chunks if c['id'] in read],
                notes=notes, shared_scope_identity=sid, shared_read_policy=SHARED_POLICY,
                read_batches=[dict(batch_id=b['batch_id'], chunk_ids=[chunkmap[i] for i in b['chunk_ids']])
                    for b in cache['batches']])

    def _task(self, f):
        if f['id'] in self.by_finding:
            progress=f['content_read_progress']
            if progress.get('pending_task'):
                return progress['pending_task']
            if set(progress['read_chunk_ids'])=={c['id'] for c in _chunks(f)}:
                return dict(mode='synthesize',chunk_ids=[],repair=False)
        return super()._task(f)

    def step(self, budget):
        f=next((f for f in self.findings if not f.get('review_blocked') and
            not f.get('semantic_review',{}).get('validated_scope')), None)
        sid=self.by_finding.get(f['id']) if f else None
        if not sid:
            return super().step(budget)
        fs=self.groups[sid]; leader=fs[0]; cache=self.cache[sid]
        read={i for b in cache['batches'] for i in b['chunk_ids']}
        unread=[c for c in _chunks(leader) if c['id'] not in read]
        if not unread:
            return super().step(budget)
        selected=[]; count=characters=0
        for c in unread:
            size=len(_evidence([c]))
            if characters+len(c['text'])>MAX_BATCH_CHARACTERS or count+size>MAX_BATCH_EVIDENCE:
                break
            selected.append(c['id']); characters+=len(c['text']); count+=size
        if not selected:
            raise ValueError('联合阅读单片段超出授权请求范围。')
        while selected:
            packet,evidence,messages,upper=self._shared_packet(leader,fs,cache,selected,budget)
            if budget.can_request(upper):
                break
            if len(selected)==1:
                hard_limit=self.state.get('max_reserved_token_bound')
                if isinstance(hard_limit,(int,float)) and upper>hard_limit:
                    for x in fs:
                        self._block(x,'单个完整片段与全部教师要求的保守预算上界为'+str(upper)+
                            '，超过本轮上限'+str(hard_limit)+'；未删减原文或教师要求。',
                            'shared_minimum_packet_exceeds_budget')
                    self._save()
                return False
            selected=selected[:-1]
        self.state['current_request']=dict(phase='content',kind='shared_read',ids=[x['id'] for x in fs],chunk_ids=selected)
        self.state.setdefault('tool_calls',[]).append(dict(name='read_verified_content_scope',finding_id=leader['id'],
            shared_rule_ids=packet['joint_rule_ids'],chunk_ids=selected,source_sha256=self.report['source_sha256']))
        self._save()
        pending=cache.get('pending_error')
        try:
            choice=budget.request(messages,response_format={'type':'json_object'})
            if choice.finish_reason!='stop' or getattr(choice.message,'tool_calls',None):
                raise ValueError('联合阅读输出未正常完成。')
            from new_paper_agent import strict_json
            value=_canonical_response(strict_json(choice.message.content),
                {f'E{i+1}':canonical for i,canonical in enumerate(evidence)})
            if (not isinstance(value,dict) or set(value)!={'batch_id','observations'} or
                value['batch_id']!=packet['batch_id'] or not isinstance(value['observations'],list) or
                len(value['observations'])>2*len(packet['check_dimensions']) or
                not all(_valid_note(n,dict(check_dimensions=packet['check_dimensions']),evidence) for n in value['observations'])):
                raise ValueError('联合阅读输出与原文或维度绑定不符。')
            cache['batches'].append(dict(batch_id=packet['batch_id'],chunk_ids=selected,notes=value['observations']))
            cache.pop('pending_error',None)
            self._apply(sid)
        except ValueError as error:
            if self.state.get('unknown_cost_attempts'):
                raise
            self.state.setdefault('phase_errors',[]).append(dict(phase='shared_content',ids=[x['id'] for x in fs],error=str(error)))
            if pending:
                for x in fs:
                    self._block(x,'联合阅读一次修复后仍未通过校验：'+str(error),'shared_output_validation_failed')
            else:
                cache['pending_error']=str(error)
        finally:
            self.state.pop('current_request',None)
            self._save()
        self.findings=self.findings[1:]+self.findings[:1]
        return True

    def _shared_packet(self,leader,fs,cache,selected,budget):
        task=dict(mode='read',chunk_ids=selected,repair=False)
        packet,evidence=super()._packet(leader,task)
        packet['check_dimensions']=self._all_dimensions(fs)
        packet['teacher_quotes']=list(dict.fromkeys(q for x in fs for q in
            [e.get('quote',e.get('comment_text','')) for e in self.rules.get(x['rule_id'],{}).get('teacher_evidence',[]) ]
            if isinstance(q,str) and q.strip()))
        packet['joint_rule_ids']=[x['rule_id'] for x in fs]
        from general_review_rules import VERSION, POLICY
        general = [self.rules.get(x['rule_id'], {}).get('general_requirements') for x in fs]
        if all(isinstance(g, dict) and g.get('version') == VERSION for g in general):
            packet['teacher_quotes'] = []
            packet['general_requirements'] = dict(version=VERSION, application_policy=POLICY,
                rules=[dict(rule_id=x['rule_id'], **g) for x,g in zip(fs,general)])
        packet['policy']='联合阅读全部给定教师要求，分别记录各维度；本阶段不输出最终规则判断。'
        if not packet['teacher_quotes'] and packet.get('general_requirements'):
            packet['policy'] = POLICY
        packet['scope_context_key']=cache['group_key']
        packet['batch_id']='S'+_hash(dict(group_key=cache['group_key'],chunk_ids=selected))[:32]
        pending=cache.get('pending_error')
        if pending:
            packet['output_repair']=dict(error=pending,instruction='仅修复本批格式和证据对应，最多一次。')
        messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(packet,ensure_ascii=False))]
        _,upper=budget.prepare(messages,response_format={'type':'json_object'})
        return packet,evidence,messages,upper
