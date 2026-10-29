"""Assemble a verified local stage report without API, SVN or Word calls."""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from cross_student_rule_pilot import digest
from paper_pipeline import read, save, verify_artifacts, ROOT, REVIEW, ACCEPTED
from reviewed_rule_catalog import BATCH, load_catalog
from scope_object_alignment import collect_alignment
from scoped_rule_checks import ScopedRuleTool
import three_rule_pilot_agent_v3 as agent

SCOPED = BATCH/'scoped_rule_check_20261007_210311_082650'
ALIGNED = BATCH/'object_alignment_20261007_211430_400071'
CHAPTERS = ROOT/'deliverables/章节人工复核_20261007_133700.json'


def link(label, path):
    return f'[{label}](<{Path(path).resolve().as_posix()}>)'


async def collect_verified():
    # Fresh readers validate pinned context/layout receipts and source lineage.
    accepted = REVIEW/ACCEPTED
    proof = read(accepted/'integration_verification.json')
    if proof['status'] != 'verified_real_v3_model_integration':
        raise ValueError('Three-student integration receipt missing')
    verify_artifacts(proof['artifacts'])
    old = read(accepted/'run.json')
    for name, sha in old['code_hashes'].items():
        if digest(ROOT/name) != sha: raise ValueError('Accepted agent code changed: '+name)
    tools = {name: (agent.VerifiedTableTool(REVIEW, REVIEW/agent.RECEIPT_RELATIVE)
                   if name == 'load_verified_table_pagination' else agent.BatchTool(name, REVIEW))
             for name in agent.NAMES}
    for tool in tools.values(): await tool.execute()
    final = read(accepted/'final_report.json')
    if final != {'items': agent.expected_rows(tools),
                 'teacher_reference': tools['load_verified_table_pagination'].teacher_reference}:
        raise ValueError('Three-student summary differs from fresh evidence')
    for name, tool in tools.items():
        if read(accepted/(name+'_evidence.json')) != tool.full_result:
            raise ValueError('Three-student full evidence changed')
    catalog = load_catalog(BATCH)
    for folder, status in [(SCOPED, 'verified_real_local_scoped_check'),
                           (ALIGNED, 'verified_local_object_alignment_run')]:
        receipt = read(folder/'integration_verification.json')
        if receipt['status'] != status: raise ValueError('Missing local acceptance')
        verify_artifacts(receipt['artifacts'])
        run = read(folder/'run.json')
        for name, sha in run['code_hashes'].items():
            if digest(ROOT/name) != sha: raise ValueError('Local execution code changed: '+name)
        if any(run[key] != 0 for key in ('model_requests','svn_requests','word_launches','new_tokens')):
            raise ValueError('Local stage cost differs')
    scoped = read(SCOPED/'checks.json')
    if scoped['source'] != catalog['initial_source']: raise ValueError('Scoped source differs')
    for rule, saved in zip(catalog['rules'], scoped['rules']):
        tool = ScopedRuleTool(rule, catalog['initial_source']); await tool.execute()
        if tool.result != saved or read(SCOPED/(rule['rule_id']+'_evidence.json')) != saved:
            raise ValueError('Scoped full evidence differs')
    if len(scoped['rules']) != 6: raise ValueError('Expected six scoped conditions')
    alignment = collect_alignment(BATCH)
    if alignment != read(ALIGNED/'alignment.json'): raise ValueError('Object alignment differs')
    structures = tools['inspect_three_initial_chapters'].full_result
    chapters = read(CHAPTERS)
    if len(chapters['items']) != 2: raise ValueError('Expected two existing chapter reviews')
    for saved in chapters['items']:
        fresh = next(s for s in structures if s['source']['sample_id'] == saved['source']['sample_id'])
        if saved['source'] != fresh['source'] or saved['headings'] != fresh['relevant_headings']:
            raise ValueError('Chapter review no longer matches source/outline')
        if saved['review_origin'] != 'explicit_conversation_confirmation' or saved['status'] != 'confirmed_structure_issue_under_teacher_policy':
            raise ValueError('Chapter manual attribution differs')
    context = tools['triage_three_initial_texts'].full_result['review']
    affected = {(d['source']['sample_id'], r['paragraph_index']) for d in context['documents'] for r in d['reviewed_items']}
    cross = read(REVIEW.parent/'run.json')
    if cross['selected_comment_count'] != 34 or cross['independent_student_count'] != 3:
        raise ValueError('Cross-student collection scope changed')
    return {'three_student_final': final, 'chapter_manual_reviews': chapters['items'],
            'affected_first_person_paragraphs': len(affected), 'cross_student_rules': read(REVIEW/'reviewed_rules.json'),
            'p001_pipeline_acceptance': read(BATCH/'integration_verification.json'),
            'scoped_catalog': catalog, 'scoped_checks': scoped, 'object_alignment': alignment,
            'three_student_cost': {k: old[k] for k in ('request_attempts','known_total_tokens','unknown_cost_attempts','max_request_attempts','max_output_tokens','soft_total_token_threshold','strict_total_token_budget')},
            'cross_student_analysis': {k: cross[k] for k in ('selected_comment_count','independent_student_count','usage','request_attempts')}}


def create_report(data, folder):
    final = data['three_student_final']; initial = final['items']; p001 = data['p001_pipeline_acceptance']
    rows = ['| 学生/版本 | 我字原始线索 | 范围/词义排除 | 语境复核待改次数 | 用户确认/Codex复核 | 章节结论 | 续表结论 |',
            '|---|---:|---:|---:|---|---|---|']
    chapters = {'P001': '适用范围待确认；版面可见5章，大纲不足', 'P002': '用户确认：需求分析与系统设计合章',
                'P003': '用户确认：需求分析为系统总体设计的下级标题'}
    tables = {'P001': '已审阅24页，未观察到跨页表格状对象', 'P002': '两处跨页缺续表，已视觉核验',
              'P003': '两张原生表均单页；图片覆盖有限'}
    for row in initial:
        rows.append(f'| {row["sample_id"]} r{row["revision"]} | {row["raw_wo_count"]} | {row["excluded_count"]} | {row["reviewed_change_candidate_count"]} | {row["user_confirmed_count"]}/{row["codex_reviewed_count"]} | {chapters[row["sample_id"]]} | {tables[row["sample_id"]]} |')
    body = f'''# SVN 论文检查智能体：小批次统一验收报告

验收结论：固定三学生开发回查与 P001 可配置链路已完成阶段验收，验证了“教师版本导出—批注证据提取—候选归纳—初稿检查—受控工具调用”的可行性。当前成果是小批次原型，老师要求的完整系统尚未完成。

## 1. 对照老师任务

| 要求 | 本阶段已验证 | 当前边界 |
|---|---|---|
| 从 SVN 导出教师提交且带批注的版本 | 固定作者、路径、版本导出；保存清单、作者、时间及 SHA256；DOC 转换保留原件与转换记录 | 已验证所选学生，未完成整仓库；部分旧 DOC 仅恢复文字，视觉与 Word 退出状态仍有历史待办 |
| 将批注归纳为同类常见问题并保留证据 | 三学生 {data['cross_student_analysis']['selected_comment_count']} 条可文字分析批注；经语义复核保留 XR001～XR003 三类同类反馈，含教师原话及选区 | 候选及类别均受采样范围限制；未覆盖全部反馈，不代表全仓库频率统计 |
| 使用检查项检查学生初稿 | 对 P001 r194、P002 r201、P003 r246 做文字、大纲、分页与人工/Codex 辅助复核 | 属于同一开发样本的回查；新学生泛化与独立准确率未测试 |
| 改造现有 agent 框架，目标专一、成本可控 | 复用 nanobot Tool/ToolRegistry，新增论文工具、有界模型循环、来源及最终 JSON 核验；独立程序对接 DeepSeek | 不是直接使用 nanobot 通用完整聊天循环，也未部署 CC SDK/opencode；“全程自主”尚未达成，仍依赖人工与既有固定收据 |

教师要求作为项目验收依据，论文及批注内容作为待分析数据；文档里的文字不充当程序指令。

## 2. 三学生初稿回查结果

| 已复核类别 | 同类教师反馈来源 | 检查方式 |
|---|---|---|
| XR001 第一人称表述 | P001、P003 | 本地文字线索加逐项语境复核 |
| XR002 需求分析与系统设计分章 | P002、P003 | Word大纲线索加人工结构确认 |
| XR003 跨页表格续表标识 | P001、P003 | 真实分页和页面视觉核验 |

同类反馈在所选样本中重复出现，尚不代表在完整仓库中达到统计意义上的“常见”。

{chr(10).join(rows)}

第一人称共 30 次已复核待改表述，涉及 {data['affected_first_person_paragraphs']} 个段落：用户既有确认 3 次、Codex 辅助复核 27 次。这是本轮教师规则下的表述出现次数，不是 30 个独立错误，也不是准确率；不推广为所有学术论文都禁止第一人称。P002 的第一人称检查依据跨学生试验政策及用户确认，非其教师直接第一人称批注。

章节规则限定该教师要求的系统设计类论文。P002/P003 的人工结论单独保存，分页工具的“未评估章节”状态不撤销这些结论；P001 不能从五个可见章标题推断符合此规则。学生版本来源受已保存 SVN 路径历史限制，不声称是作者从未入库的第一份稿。

P002 两处缺续表对应表3-3 sys_menu表（物理页14～15）和表3-11 exam表（物理页16～17）。P003 教师 r249 的表4-1在物理页23～24跨页且缺续表，单列为教师稿对照；学生 r246 早于 r249，不能当作批改后修复证据。分页结论限定已验收带批注/修订呈现模式（ExportItem=7）；P003 原批注锚点为表4-2，视觉确认的问题对象为表4-1，二者分别保留。

## 3. P001 可配置链路与后续检查

实际导出教师 r300/r247、学生 r194，四阶段完成。提取 {p001['comments']} 条批注，其中 {p001['nonempty_comments']} 条有文字，本次仅选 {p001['selected_comments']} 条归纳，生成 {p001['candidate_groups']} 组模型候选。语义复核拆分了公式/图编号、图片展示/表题位置等混组，形成六个局部条件。它们与跨学生三类反馈存在重叠，不能相加成九类常犯错误。

| 局部条件 | 当前结果 | 未完成部分 |
|---|---|---|
| PR001 正文第一人称 | r194 复用来源相同的 11 次已复核待改记录（用户1、Codex10） | 不做新版本自动语义判错 |
| PR002 公式编号 | partial：同段OMML编号0、图片编号线索4、未绑定1、编号文字5 | 图片及未绑定标签待核验；文字重复组0不代表公式编号全部正确 |
| PR003 图编号 | 教师C011模型2图未确认初稿对象 | 图身份与正确编号条件待明确 |
| PR004 局部层次编号 | C007 RNN标题对应r194 XML段落144；C009异常值检测对应段落175 | 编号标准仍待明确；初稿没有C009教师选区的裸2；C005 KDJ说明未找到可靠文字对应 |
| PR005 图片展示及可读性 | C010数据分布图未确认初稿对象 | 内容及真实版面待核验，无自动尺寸阈值 |
| PR006 表题位置 | C012训练结果表未确认初稿对象 | 原生表0不等于无图片表；真实版面上下位置未判定 |

对象对齐共检索六个教师目标，两处内容对应、四处未确认，不能解释为两项规则通过。未对应不证明对象不存在。完整证据保留图片关系、字节及像素哈希，未做 OCR。续表及“后面还有一个谢辞”两条未选批注仍保留在提取结果中；谢辞是否重复尚未核定。

## 4. 框架改造与成本

实现方式：在现有 nanobot 工具抽象和注册机制之上新增论文只读工具，独立控制模型循环、可调用工具、请求次数和最终字段；来源和计数由本地代码核验。导出、XML 提取、字符/大纲/标签线索尽量在本地完成，模型接收受限证据或工具摘要。固定语境和分页收据可以复用，避免为同一批结果重复发送整篇论文。

| 已保存运行 | 模型请求 | 已知token | 说明 |
|---|---:|---:|---|
| 三学生批注归纳 | 1 | 4026 | 34条可文字分析批注，候选后经语义复核 |
| 三学生最新三工具智能体 | 2 | 3020 | 实际模型工具调用，最终字段与工具证据核验一致 |
| P001可配置四阶段链路 | 1 | 2124 | 5次SVN请求尝试完成，未启动Word |
| P001范围检查及对象对齐 | 0 | 0 | 两次本地运行，分别六工具、四工具 |
| 本次报告汇总与验收 | 0 | 0 | 只读本地证据，不重新请求模型、SVN或Word |

本表只列选定历史运行，不是项目累计 token；未包含早期试验及失败请求成本。三学生最新循环最多4次请求、每次输出最多1000，8000是已观测累计用量的软停止阈值，单次请求可能越过阈值；请求字节上限也不是精确token预算。P001候选归纳最多1次请求、输出上限1600。当前不能声称已实现严格项目总预算；失败/未知用量记录保留且不自动重试。

## 5. 验收方法及最少剩余工作

本次重新读取固定三学生只读工具、P001范围工具及对象对齐结果，逐字段比较保存证据；核对来源、代码、报告、模型原始记录与接入验收清单的哈希。既有章节人工记录重新绑定当前初稿大纲。未改论文、旧模型结果或历史阶段日志。

后续按以下顺序推进：

1. 固定三类已有规则及其适用范围，明确自动线索、语境复核、版面复核的不同状态；把章节既有人工结论纳入摘要，P001对象/公式证据不足项保留待核验。
2. 选择未参与规则归纳的新学生；先保存初稿并锁定规则，再做独立人工标注，分别统计可检查范围内的误报、漏报及覆盖率。允许“无法检查”，不把未查计为正确。
3. 将新的文字判定和必要版式工具接入受控程序，减少对固定人工收据的依赖；完善旧DOC转换及更多批注类型。
4. 做运行前预算分配、跨阶段用量记账、未知用量停止以及部署/异常恢复验证；扩大到更多学生后再判断完整任务是否完成。

## 6. 证据与演示入口

- {link('来源及证据索引', folder/'证据索引.md')}：原件、教师批注、人工/视觉记录和各轮账本。
- {link('汇总数据', folder/'summary.json')}：三个开发样本、六项局部条件及当前覆盖状态。
- {link('演示与新样本验证安排', folder/'演示与下一步.md')}：不新增付费调用的展示步骤和下一批安排。

报告仅为小批次阶段成果；没有精确率/召回率结果，也没有整仓库完成声明。全部正文结论可从本机证据索引追溯。
'''
    (folder/'小批次统一验收报告.md').write_text(body, encoding='utf-8')
    brief = '''# 向老师汇报的阶段说明

目前完成了基于 nanobot 工具接口的论文检查原型：从 SVN 导出教师历史版本、保留批注与选区证据，使用 DeepSeek 归纳候选问题，再结合本地文字、大纲和 Word 页面证据回查学生早期稿。程序通过工具白名单、请求次数限制、来源哈希和最终结果校验约束运行，没有使用 CC SDK 或 opencode 部署。

小批次包含三名学生及34条可文字分析批注，人工/Codex辅助复核保留了第一人称、需求分析与系统设计分章、跨页续表三类同类反馈。三学生回查记录有30次第一人称待改表述（23段，用户确认3次、Codex复核27次），两名学生的章节问题有用户确认，P002两处缺续表已有页面证据。

P001演示配置已实际跑通导出、提取、候选归纳、初稿线索检查四阶段，消耗2124 token；三学生最新受控智能体运行消耗3020 token。两者分别是单轮用量。后续来源相同的范围检查、对象对齐及本次汇总均在本地完成，没有新增项目API用量。

当前结果验证了小批次流程可行性，仍有人工与固定证据依赖。图片公式、部分图表身份及规则适用尚待核验；不是所有批注均已覆盖，也未开展独立新学生准确率测试。下一步固定规则后进行新学生验证，再补总预算控制、格式覆盖和部署验证。完整任务仍在推进。
'''
    (folder/'给老师的阶段汇报.md').write_text(brief, encoding='utf-8')
    demonstration = f'''# 演示与下一步

先打开本目录的“给老师的阶段汇报.md”，再看“小批次统一验收报告.md”的结果和边界。不要以历史日志开头的旧状态作为当前结论。

## 无新增项目API调用的演示

1. 展示SVN导出来源清单及教师批注：{link('P001教师清单', BATCH/'export/P001/teacher_exports/manifest.json')}，{link('完整批注证据', BATCH/'extract/P001/report.md')}。
2. 展示跨学生三类别、教师原话与原选区：{link('已复核三类规则', REVIEW/'reviewed_rules.json')}。
3. 展示三学生结果与人工/视觉归属：{link('最新智能体报告', REVIEW/ACCEPTED/'report.md')}，以及证据索引中的章节和语境复核。
4. 展示P001四阶段账本及混组收窄：{link('四阶段验收', BATCH/'integration_verification.json')}，{link('候选语义复核', BATCH/'semantic_review.md')}。
5. 展示六工具及对象对齐的覆盖限制；“未确认”保持原样，不改成通过。

如需现场复读三学生已验收结果，可以运行既有命令；它会生成新的本地replay目录，不调用模型、SVN或Word：

```powershell
Set-Location -LiteralPath "D:\\svn_agent"
.\\.venv\\Scripts\\python.exe "D:\\svn_agent\\paper_pipeline_v3.py" replay
```

## 新学生验证安排（每天1～2小时，按实际处理难度调整）

- 第1次：选一位不属于P001/P002/P003的学生，锁定待检查稿的SVN版本与原件；检查前锁定三类规则与范围，不从其教师批注生成新规则。
- 第2次：对可检查正文、章节和相关表格做独立人工标注；保存适用/不适用/无法判定及原文位置，与检测结果分开保存。
- 第3次：运行受控检查并逐项对照；新语境不能复用旧学生的语境判定。图片/分页需要实际页面证据，不用文本关键词代替。
- 第4次：整理误报、漏报、覆盖不足及本轮真实成本。若据新样本反馈调整规则，该学生转为开发样本，不能继续充当同规则的独立测试。

新样本精确率、召回率和覆盖率需基于事先确定的同一统计单位与适用范围；30次命中、23段以及2处表格不能直接合并成准确率分母。最终报告保留“无法检查”并说明原因。

本目录是本机报告与证据索引，未复制全部论文、页面及API原始记录。发送给老师时，可直接使用阶段汇报及统一报告正文；完整复现依赖索引里的原工作区文件。
'''
    (folder/'演示与下一步.md').write_text(demonstration, encoding='utf-8')


async def assemble():
    data = await collect_verified()
    folder = ROOT/'deliverables'/('小批次统一验收_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(exist_ok=False)
    roles = {
      '三学生最新模型验收': REVIEW/ACCEPTED/'integration_verification.json',
      '三学生最新模型账本': REVIEW/ACCEPTED/'run.json',
      '跨学生归纳账本': REVIEW.parent/'run.json',
      '跨学生三类规则': REVIEW/'reviewed_rules.json',
      '章节既有人工确认': CHAPTERS,
      '逐项第一人称语境复核': REVIEW/'first_person_context_review_20261007_202622_417418/review.json',
      '语境复核副本': REVIEW/'first_person_context_review_20261007_202622_417418/核查表_语境复核副本.md',
      '分页视觉验收': REVIEW/'remaining_initial_layout_20261007_194115_597974/visual_review/verification.json',
      '分页视觉报告': REVIEW/'remaining_initial_layout_20261007_194115_597974/visual_review/report.md',
      'P001四阶段验收': BATCH/'integration_verification.json',
      'P001四阶段账本': BATCH/'run.json',
      'P001候选语义复核': BATCH/'semantic_review.json',
      'P001范围检查验收': SCOPED/'integration_verification.json',
      'P001范围结果': SCOPED/'checks.json',
      'P001对象对齐验收': ALIGNED/'integration_verification.json',
      'P001对象对应结果': ALIGNED/'alignment.json',
    }
    # Include the actual inspected source files; these are references, not copies.
    for row in data['object_alignment']['teacher_inventories']+[data['object_alignment']['initial_inventory']]:
        s = row['source']; roles[s['sample_id']+'_r'+str(s['revision'])+'_对象来源'] = Path(s['document_path'])
    for chapter in data['chapter_manual_reviews']:
        s = chapter['source']; roles[s['sample_id']+'_r'+str(s['revision'])+'_章节来源'] = Path(s['document_path'])
    sources = [{'role': role, 'path': str(path), 'sha256': digest(path)} for role, path in roles.items()]
    save(folder/'summary.json', {'status': 'verified_development_pilot_stage_delivery',
        'assembled_at': datetime.now().isoformat(), 'new_model_requests': 0, 'new_svn_requests': 0,
        'new_word_launches': 0, 'new_tokens': 0, 'independent_accuracy_test': False,
        'full_teacher_project_completed': False, 'data': data})
    save(folder/'evidence_index.json', {'sources': sources, 'scope': 'Local working-copy references, not a portable archive of all original artifacts.'})
    lines = ['# 证据索引', '', '以下记录在汇总时已核对来源和完整证据；不同版本、规则和复核来源分别保留。', '', '| 材料 | 文件 | SHA256 |', '|---|---|---|']
    for row in sources: lines.append('| '+row['role']+' | '+link('打开', row['path'])+' | `'+row['sha256']+'` |')
    (folder/'证据索引.md').write_text('\n'.join(lines), encoding='utf-8')
    create_report(data, folder)
    if await collect_verified() != data: raise ValueError('Sources changed while assembling report')
    verify_artifacts(sources)
    outputs = [{'path': str(path), 'sha256': digest(path)} for path in sorted(folder.iterdir()) if path.is_file()]
    manifest = {'status': 'verified_local_report_assembly', 'new_tokens': 0, 'new_external_requests': 0,
                'independent_accuracy_test': False, 'full_teacher_project_completed': False,
                'generator': {'path': str(Path(__file__)), 'sha256': digest(Path(__file__))},
                'sources': sources, 'outputs': outputs, 'verified_against_fresh_local_evidence': True}
    save(folder/'验收清单.json', manifest)
    verify_artifacts(outputs)
    return folder


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print('读取并核对既有来源、工具结果及复核记录；不请求模型/SVN/Word。', flush=True)
    print('交付目录：', asyncio.run(assemble()))
