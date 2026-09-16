"""GUI form for source-bound MEP profiles. Codex proposals use the same form and save gate."""
from collections import Counter
import copy
import json
import math
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk


def mep_property_overrides(category, width, height):
    keys = ('diameter', None) if category == 'pipe' else ('width_mm', 'height_mm') if category in ('duct', 'tray') else ('width', 'height')
    result = {}
    for key, value in zip(keys, (width, height)):
        if key and str(value).strip():
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                raise ValueError('치수는 0보다 커야 합니다.')
            result[key] = number
    return result


def blender_review_status(receipt):
    """Native file validity is distinct from unresolved source-drawing findings."""
    source = receipt.get('source_verification', {})
    warnings = [item for item in source.get('findings', []) if item.get('severity') == 'warn']
    diagnostics = receipt.get('diagnostics', [])
    count = source.get('warnings', len(warnings))
    if not isinstance(count, int):
        count = len(warnings)
    detail = [f"{item.get('id', item.get('code', '검토'))}: {item.get('message', str(item))}"
              if isinstance(item, dict) else str(item) for item in warnings + diagnostics]
    return f"파일 검증 완료 / 도면 검토 {count + len(diagnostics)}건 남음", detail


class MepSetupDialog:
    def __init__(self, parent, session, inventory, on_saved, source_id=None):
        self.session, self.inventory, self.on_saved = session, inventory, on_saved
        self.manifest = session.store.refresh_inputs()
        self.source_id = source_id or self.manifest['sources'][0]['id']
        self.proposal = None
        self.mappings = []
        self.win = tk.Toplevel(parent)
        self.win.title('설비 도면 설정 · 프로젝트에 저장')
        self.win.geometry('1150x860')
        self.win.minsize(980, 740)
        ttk.Label(self.win, text='원본 영역과 레이어를 선택하고 실제 치수·높이를 입력하세요. 가정값은 검토 필요로 남습니다.').pack(anchor='w', padx=12, pady=8)
        self.tabs = ttk.Notebook(self.win)
        self.tabs.pack(fill='both', expand=True, padx=10)
        self.mapping_tab, self.level_tab, self.proposal_tab = (ttk.Frame(self.tabs) for _ in range(3))
        for page, name in [(self.mapping_tab, '영역·레이어'), (self.level_tab, '높이·바닥 구성'), (self.proposal_tab, 'Codex 제안 검토')]:
            self.tabs.add(page, text=name)
        self._build_mapping()
        self._build_levels()
        self._build_proposals()
        self.source_tab, self.architecture_tab = ttk.Frame(self.tabs), ttk.Frame(self.tabs)
        self.tabs.insert(1, self.source_tab, text='설비 원본 필터')
        self.tabs.insert(2, self.architecture_tab, text='건축 분류')
        self._build_source_filters()
        self._build_architecture()
        # 핸들·정규식(설비 원본 필터)과 MCP 제안 검토는 현장에서 쓸 일이 없다 — 만들어 두고 숨긴다.
        # 위젯은 그대로 살아 있어 폼 값 왕복과 저장 경로는 바뀌지 않는다.
        self.expert_tabs = (self.source_tab, self.proposal_tab)
        self.v_expert = tk.BooleanVar(value=False)
        self._toggle_expert()
        self.status = tk.StringVar(value='원본 SHA-256: ' + inventory['source_sha256'][:20])
        ttk.Label(self.win, textvariable=self.status, wraplength=1050).pack(anchor='w', padx=12, pady=6)
        controls = ttk.Frame(self.win)
        controls.pack(fill='x', padx=10, pady=8)
        self.save_button = ttk.Button(controls, text='설정 저장 · 다시 모델링', command=self._save)
        self.save_button.pack(side='right', padx=5)
        ttk.Button(controls, text='닫기', command=self.win.destroy).pack(side='right')
        ttk.Checkbutton(controls, text='전문가 설정 보기 (원본 핸들 · 제안 검토)',
                        variable=self.v_expert, command=self._toggle_expert).pack(side='left')
        source = next(s for s in self.manifest['sources'] if s['id'] == self.source_id)
        current = source.get('options', {}).get('mep_profile') or {}
        self._load_profile(current)
        self._draw_regions()

    def _toggle_expert(self):
        for tab in self.expert_tabs:
            self.tabs.add(tab) if self.v_expert.get() else self.tabs.hide(tab)

    def _entry(self, parent, label, row, values=None, width=17):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=4, pady=3)
        var = tk.StringVar()
        control = ttk.Combobox(parent, textvariable=var, values=values, width=width) if values else ttk.Entry(parent, textvariable=var, width=width)
        control.grid(row=row, column=1, sticky='ew', padx=4, pady=3)
        return var

    def _build_mapping(self):
        upper = ttk.Frame(self.mapping_tab)
        upper.pack(fill='x', pady=5)
        ttk.Label(upper, text='모델링 영역').pack(side='left', padx=6)
        self.regions = self.inventory.get('regions', [])
        self.region_names = ['전체 도면 (여러 배치가 있으면 영역을 선택하세요)'] + [f"{r['id']} · {r.get('label', r['id'])}" for r in self.regions] + ['직접 지정 (아래 범위 mm)']
        self.region = tk.StringVar()
        self.region_select = ttk.Combobox(upper, textvariable=self.region, values=self.region_names, state='readonly', width=68)
        self.region_select.pack(side='left', padx=5)
        self.region_select.bind('<<ComboboxSelected>>', lambda _: self._draw_regions())
        custom = ttk.Frame(self.mapping_tab)
        custom.pack(fill='x', padx=6, pady=3)
        self.region_vars = {}
        for key, label, width in [('id', '영역 ID', 15), ('xmin', 'X 최소', 12), ('ymin', 'Y 최소', 12), ('xmax', 'X 최대', 12), ('ymax', 'Y 최대', 12)]:
            ttk.Label(custom, text=label).pack(side='left', padx=3)
            var = tk.StringVar()
            self.region_vars[key] = var
            ttk.Entry(custom, textvariable=var, width=width).pack(side='left')
        self.canvas = tk.Canvas(self.mapping_tab, height=155, background='#f1f5f9', highlightthickness=0)
        self.canvas.pack(fill='x', padx=6)
        self.canvas.bind('<Configure>', lambda _: self._draw_regions())
        self.canvas.bind('<Button-1>', self._pick_region)
        middle = ttk.Frame(self.mapping_tab)
        middle.pack(fill='both', expand=True, padx=6, pady=4)
        left = ttk.Frame(middle)
        left.pack(side='left', fill='both', expand=True)
        ttk.Label(left, text='원본 레이어 (여러 개 선택 가능) · 색상/선종류는 필터에 사용할 수 있습니다.').pack(anchor='w')
        self.layer_tree = ttk.Treeview(left, columns=('name', 'count', 'types', 'colors'), show='headings', height=8, selectmode='extended')
        for key, label, width in [('name', '레이어', 270), ('count', '도형 수', 65), ('types', '도형 종류', 120), ('colors', '색상', 70)]:
            self.layer_tree.heading(key, text=label)
            self.layer_tree.column(key, width=width, stretch=key == 'name')
        self.layer_tree.pack(fill='both', expand=True)
        self.layers = self.inventory.get('layers', [])
        for i, row in enumerate(self.layers):
            self.layer_tree.insert('', 'end', iid=str(i), values=(row['name'], row['count'], ', '.join(row.get('entity_types', {})), ','.join(map(str, row.get('colors', [])))))
        form = ttk.Frame(middle)
        form.pack(side='right', fill='y', padx=8)
        self.rule_vars = {}
        for i, (key, label, values) in enumerate([
            ('category', '형상', ['pipe', 'duct', 'tray', 'equipment']), ('system', '계통 / 회로', ['heating', 'SA', 'RA', 'OA', 'EA']),
            ('representation', '원본 표현', ['centerline', 'outline']), ('diameter_mm', '실외경 mm (원형)', None),
            ('width_mm', '폭 mm (사각)', None), ('height_mm', '높이 mm (사각/기구)', None),
            ('nominal_size', '공칭 규격 (예: 15A)', None), ('material', '재질', ['PB', 'PVC', 'steel']),
            ('placement', '설치 기준', ['source', 'center', 'slab_soffit', 'foam_top']),
            ('center_elevation_mm', '중심 높이 mm', None), ('dimension_basis', '치수 근거', ['user', 'assumed', 'annotation']),
            ('color', '색상 필터 (선택)', None), ('linetype', '선종류 필터 (선택)', None)]):
            self.rule_vars[key] = self._entry(form, label, i, values)
        for key, value in {'category': 'pipe', 'system': 'heating', 'representation': 'centerline', 'material': 'PB', 'placement': 'foam_top', 'dimension_basis': 'assumed'}.items():
            self.rule_vars[key].set(value)
        ttk.Button(form, text='선택 레이어에 설정 추가', command=self._add_mapping).grid(row=13, column=0, columnspan=2, pady=5)
        ttk.Button(form, text='선택 규칙 수정', command=self._update_mapping).grid(row=14, column=0, columnspan=2, pady=3)
        ttk.Button(form, text='선택 규칙을 외곽선 폭별로 나누기', command=self._split_by_outline).grid(row=15, column=0, columnspan=2, pady=3)
        ttk.Button(form, text='선택 장비 규칙의 본체 고르기', command=self._pick_equipment_body).grid(row=16, column=0, columnspan=2, pady=3)
        # 평면도의 z 는 0 이다 — 'source' 로 두면 그 0 이 설치 높이가 되어 설비가 바닥에 깔린다.
        self.placement_hint = tk.StringVar(value='')
        ttk.Label(form, textvariable=self.placement_hint, wraplength=210,
                  foreground='#a06000').grid(row=17, column=0, columnspan=2, sticky='w', padx=4)
        def _placement_hint(*_args):
            self.placement_hint.set('평면 z 를 그대로 씁니다. 평면도의 z 는 0 이라 설치 높이가 아닙니다 — '
                                    'slab_soffit(슬래브 밑면 밀착) 또는 center 를 고르세요.'
                                    if self.rule_vars['placement'].get() == 'source' else '')
        self.rule_vars['placement'].trace_add('write', _placement_hint)
        _placement_hint()
        self.map_tree = ttk.Treeview(left, columns=('pattern', 'category', 'system', 'dimension', 'placement'), show='headings', height=5)
        for key, label, width in [('pattern', '저장할 규칙', 250), ('category', '형상', 55), ('system', '계통', 65), ('dimension', '치수 mm', 85), ('placement', '설치', 105)]:
            self.map_tree.heading(key, text=label)
            self.map_tree.column(key, width=width, stretch=key == 'pattern')
        ttk.Label(left, text='이번 프로젝트의 매핑 규칙').pack(anchor='w', pady=(7, 0))
        self.map_tree.pack(fill='x')
        self.map_tree.bind('<<TreeviewSelect>>', self._select_mapping)
        ttk.Button(left, text='선택 규칙 삭제', command=self._delete_mapping).pack(anchor='e', pady=3)

    def _build_source_filters(self):
        ttk.Label(self.source_tab, text='영역·레이어 탭에서 규칙을 선택하고 이 필터를 지정하세요. 원본이 두 규칙에 동시에 해당하면 저장을 차단합니다.\n원형 덕트: duct + centerline + round + 실외경. 말단/장비: equipment + outline + 높이. 기호 외곽은 검토용 형상입니다.', wraplength=920).pack(anchor='w', padx=16, pady=15)
        form = ttk.Frame(self.source_tab); form.pack(anchor='nw', padx=12)
        for i, (key, label, values) in enumerate([
            ('section_shape', '단면 형태', ['rect', 'round']), ('role', '기구 역할', ['equipment', 'terminal', 'sleeve']),
            ('block_pattern', '블록명 정규식 (선택)', None), ('entity_types', '원본 유형 (쉼표 구분)', None),
            ('source_handles', '원본 핸들 (쉼표 구분)', None)]):
            self.rule_vars[key] = self._entry(form, label, i, values, width=48)
        self.source_filter_status = tk.StringVar(value='원본 필터가 없으면 레이어·색상·선종류로 선택합니다.')
        ttk.Label(self.source_tab, textvariable=self.source_filter_status, wraplength=920).pack(anchor='w', padx=16, pady=15)
        ttk.Button(self.source_tab, text='선택 규칙 수정', command=self._update_mapping).pack(anchor='w', padx=16)

    def _build_architecture(self):
        self.architecture_rules = []
        ttk.Label(self.architecture_tab, text='현재 프로젝트에서만 레이어 역할을 지정합니다. 전역 레이어맵은 바뀌지 않습니다.\n벽·기둥 구분과 높이는 원본을 확인한 뒤 입력하세요. 결과는 부재 검토 대상으로 남습니다. 중첩 블록은 현재 건축 파서의 INSERT 레이어 기준을 따릅니다.', wraplength=920).pack(anchor='w', padx=16, pady=15)
        form = ttk.Frame(self.architecture_tab); form.pack(anchor='nw', padx=12)
        self.arch_vars = {}
        for i, (key, label, values) in enumerate([
            ('layer', '원본 레이어', [r['name'] for r in self.layers]), ('category', '역할', ['wall', 'column', 'ignore']),
            ('height_mm', '높이 mm (선택)', None), ('width_mm', '벽 기본 두께 mm (선택)', None),
            ('pair_min_mm', '벽면 짝 최소 간격 mm (벽, 비우면 50)', None)]):
            self.arch_vars[key] = self._entry(form, label, i, values, width=50)
        ttk.Button(form, text='레이어 분류 추가 / 수정', command=self._add_architecture).grid(row=5, column=0, columnspan=2, pady=8)
        self.arch_tree = ttk.Treeview(self.architecture_tab, columns=('pattern', 'category', 'height', 'width', 'pair_min'), show='headings', height=10)
        for key, label in [('pattern', '레이어 규칙'), ('category', '역할'), ('height', '높이 mm'), ('width', '기본 두께 mm'), ('pair_min', '짝 최소 간격 mm')]:
            self.arch_tree.heading(key, text=label)
        self.arch_tree.pack(fill='x', padx=16, pady=8)
        ttk.Button(self.architecture_tab, text='선택 건축 규칙 삭제', command=self._delete_architecture).pack(anchor='e', padx=16)

    def _refresh_architecture(self):
        self.arch_tree.delete(*self.arch_tree.get_children())
        for i, r in enumerate(self.architecture_rules):
            self.arch_tree.insert('', 'end', iid=str(i), values=(r['pattern'], r['category'], r.get('height_mm', ''),
                                                                  r.get('width_mm', ''), r.get('pair_min_mm', '')))

    def _add_architecture(self):
        try:
            layer = self.arch_vars['layer'].get().strip()
            if layer not in {r['name'] for r in self.layers}:
                raise ValueError('목록에서 원본 레이어를 선택하세요.')
            row = {'pattern': '^' + re.escape(layer) + '$', 'category': self.arch_vars['category'].get()}
            if row['category'] not in ('wall', 'column', 'ignore'):
                raise ValueError('벽·기둥·제외 중 역할을 선택하세요.')
            from drawing_units import positive_scale
            for key in ('height_mm', 'width_mm', 'pair_min_mm'):
                if self.arch_vars[key].get().strip():
                    row[key] = positive_scale(self.arch_vars[key].get())
            self.architecture_rules = [r for r in self.architecture_rules if r['pattern'] != row['pattern']] + [row]
            self._refresh_architecture()
        except ValueError as exc:
            messagebox.showerror('건축 분류 확인', str(exc), parent=self.win)

    def _delete_architecture(self):
        selected = set(map(int, self.arch_tree.selection()))
        self.architecture_rules = [r for i, r in enumerate(self.architecture_rules) if i not in selected]
        self._refresh_architecture()

    def _build_levels(self):
        from drawing_units_ui import unit_summary
        form = ttk.Frame(self.level_tab)
        form.pack(anchor='nw', padx=18, pady=12)
        ttk.Label(form, text='모든 높이는 바닥 구조 슬라브 윗면 기준입니다. 원본의 설치 높이가 없으면 입력값이 모델링 가정으로 기록됩니다.', wraplength=950).grid(row=0, column=0, columnspan=3, sticky='w', pady=8)
        self.unit_summary = tk.StringVar(value=unit_summary(self.inventory))
        ttk.Label(form, textvariable=self.unit_summary, wraplength=930).grid(row=11, column=0, columnspan=3, sticky='w', pady=8)
        self.level_vars = {}
        for i, (key, label) in enumerate([
            ('structural_slab_top_mm', '바닥 구조 슬라브 윗면 mm'), ('floor_to_floor_mm', '층간 높이 mm'), ('slab_thickness_mm', '슬라브 두께 mm'),
            ('impact_insulation', '차음재 두께 mm'), ('foamed_concrete', '기포콘크리트 두께 mm'), ('screed', '방통 두께 mm (관 포함)'),
            ('unit_scale_to_mm', '도면 1단위 → mm (단위 미정이면 필수)'), ('curve_chord_error_mm', '곡선 최대 현 오차 mm'),
            ('endpoint_tolerance_mm', '동일 끝점 허용오차 mm'), ('gap_review_mm', '가까운 단절 진단 거리 mm')], 1):
            self.level_vars[key] = self._entry(form, label, i)
        ttk.Button(form, text='입력 단위로 영역 다시 분석', command=self._rescan_units).grid(row=7, column=2, padx=8)
        ttk.Label(form, text='배관 공칭 15A와 실외경은 별개입니다. 실외경은 제품 자료를 확인해 입력하세요.\n가까운 끝점은 진단만 하며 자동 연결하지 않습니다. 층간 높이·바닥 두께는 다른 현장에 맞게 확인하세요.', wraplength=920).grid(row=12, column=0, columnspan=3, sticky='w', pady=14)

    def _build_proposals(self):
        ttk.Label(self.proposal_tab, text='Codex가 만든 제안은 모델을 바꾸지 않습니다. 내용을 확인하고 폼에 불러온 뒤 저장하세요.').pack(anchor='w', padx=10, pady=10)
        self.proposal_list = tk.Listbox(self.proposal_tab, height=8)
        self.proposal_list.pack(fill='x', padx=10)
        self.proposal_list.bind('<<ListboxSelect>>', self._show_proposal)
        self.proposal_text = tk.Text(self.proposal_tab, height=15, wrap='word', state='disabled')
        self.proposal_text.pack(fill='both', expand=True, padx=10, pady=8)
        row = ttk.Frame(self.proposal_tab)
        row.pack(fill='x', padx=10, pady=8)
        ttk.Button(row, text='제안 새로고침', command=self._refresh_proposals).pack(side='left')
        ttk.Button(row, text='선택 제안을 설정 폼에 불러오기', command=self._load_proposal).pack(side='right')
        self._refresh_proposals()

    def _refresh_proposals(self):
        self.proposals = [p for p in self.session.mep_proposals() if p.get('source_id') == self.source_id]
        self.proposal_list.delete(0, 'end')
        for proposal in self.proposals:
            self.proposal_list.insert('end', f"{'[만료]' if proposal['stale'] else '[검토 대기]'} r{proposal['base_revision']} · {proposal['proposal_id'][:10]} · {proposal.get('reason', '')[:90]}")

    def _show_proposal(self, _event=None):
        selected = self.proposal_list.curselection()
        if selected:
            self.proposal_text.configure(state='normal')
            self.proposal_text.delete('1.0', 'end')
            self.proposal_text.insert('1.0', json.dumps(self.proposals[selected[0]], ensure_ascii=False, indent=2))
            self.proposal_text.configure(state='disabled')

    def _load_proposal(self):
        selected = self.proposal_list.curselection()
        if not selected:
            return
        proposal = self.proposals[selected[0]]
        if proposal.get('source_id') != self.source_id or proposal.get('source_sha256') != self.inventory['source_sha256']:
            messagebox.showwarning('다른 원본의 제안', '현재 선택한 원본에 대한 제안만 불러올 수 있습니다.', parent=self.win)
            return
        if proposal['stale']:
            messagebox.showwarning('제안 만료', '프로젝트 또는 도면이 변경되었습니다. Codex에서 새 revision으로 제안을 생성하세요.', parent=self.win)
            return
        self.proposal = proposal
        self._load_profile(proposal['profile'])
        self.tabs.select(self.mapping_tab)
        self.status.set('Codex 제안을 폼에 불러왔습니다. 영역·치수·설치 기준을 검토하고 저장하세요.')

    def _load_profile(self, profile):
        self.mappings = copy.deepcopy(profile.get('layers', []))
        self.architecture_rules = copy.deepcopy(profile.get('architecture_layers', []))
        self._refresh_architecture()
        levels = profile.get('levels', {})
        defaults = {'structural_slab_top_mm': 0, 'floor_to_floor_mm': '', 'slab_thickness_mm': '',
                    'impact_insulation': '', 'foamed_concrete': '', 'screed': '',
                    'unit_scale_to_mm': self.inventory.get('scale_to_mm') or '',
                    'curve_chord_error_mm': .25, 'endpoint_tolerance_mm': .001, 'gap_review_mm': 10}
        defaults.update(levels)
        defaults.update({x['role']: x['thickness_mm'] for x in profile.get('floor_layers', [])})
        for key in ('unit_scale_to_mm', 'curve_chord_error_mm', 'endpoint_tolerance_mm', 'gap_review_mm'):
            if key in profile:
                defaults[key] = profile[key]
        for key, var in self.level_vars.items():
            var.set(str(defaults[key]))
        selected_region = profile.get('region')
        index = next((i + 1 for i, r in enumerate(self.regions) if selected_region and
                      r['id'] == selected_region.get('id') and r['bounds_mm'] == selected_region.get('bounds_mm')), 0)
        for var in self.region_vars.values():
            var.set('')
        if selected_region and index == 0:
            index = len(self.regions) + 1
            self.region_vars['id'].set(selected_region['id'])
            for key, value in zip(('xmin', 'ymin', 'xmax', 'ymax'), selected_region['bounds_mm']):
                self.region_vars[key].set(str(value))
        self.region_select.current(index)
        self._refresh_mappings()
        self._draw_regions()

    def _rescan_units(self):
        try:
            from drawing_units import positive_scale
            scale = positive_scale(self.level_vars['unit_scale_to_mm'].get())
        except ValueError as exc:
            messagebox.showerror('단위 확인', str(exc), parent=self.win)
            return
        self.save_button.state(['disabled'])
        self.status.set('입력한 단위로 원본 영역을 다시 분석합니다…')
        source = next(s for s in self.manifest['sources'] if s['id'] == self.source_id)
        def run():
            try:
                from mep_profile import inspect_mep_source
                inventory = inspect_mep_source(source['path'], unit_scale_to_mm=scale)
                self.win.after(0, lambda: self._rescan_done(inventory))
            except Exception as exc:
                self.win.after(0, lambda message=str(exc): self._failed(message))
        threading.Thread(target=run, daemon=True).start()

    def _rescan_done(self, inventory):
        if inventory['source_sha256'] != self.inventory['source_sha256']:
            self._failed('분석 중 원본 도면이 바뀌었습니다. 설정 창을 다시 여세요.')
            return
        self.inventory = inventory
        from drawing_units_ui import unit_summary
        self.unit_summary.set(unit_summary(inventory))
        self.regions = inventory.get('regions', [])
        self.region_names = ['전체 도면 (여러 배치가 있으면 영역을 선택하세요)'] + [f"{r['id']} · {r.get('label', r['id'])}" for r in self.regions] + ['직접 지정 (아래 범위 mm)']
        self.region_select.configure(values=self.region_names)
        self.region_select.set('')
        for var in self.region_vars.values():
            var.set('')
        self.proposal = None
        self.save_button.state(['!disabled'])
        self.status.set('단위 변경에 따라 영역을 초기화했습니다. 모델링 영역을 다시 선택하세요.')
        self._draw_regions()

    def _rule_from_form(self):
        rule = {key: value.get().strip() for key, value in self.rule_vars.items() if value.get().strip()}
        for key in ('diameter_mm', 'width_mm', 'height_mm', 'center_elevation_mm'):
            if key in rule:
                rule[key] = float(rule[key])
        if 'color' in rule:
            rule['color'] = int(rule['color'])
        for key in ('entity_types', 'source_handles'):
            if key in rule:
                rule[key] = [x.strip().upper() for x in rule[key].split(',') if x.strip()]
        return rule

    def _update_mapping(self):
        try:
            selected = self.map_tree.selection()
            if len(selected) != 1:
                raise ValueError('수정할 규칙 한 개를 선택하세요.')
            index = int(selected[0])
            row = {k: copy.deepcopy(v) for k, v in self.mappings[index].items() if k not in self.rule_vars}
            row.update(self._rule_from_form())
            self.mappings[index] = row
            self._refresh_mappings()
            self.map_tree.selection_set(str(index))
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc), parent=self.win)

    def _add_mapping(self):
        try:
            rule = self._rule_from_form()
            selected = self.layer_tree.selection()
            if not selected:
                raise ValueError('원본 레이어를 먼저 선택하세요.')
            for index in selected:
                pattern = '^' + re.escape(self.layers[int(index)]['name']) + '$'
                self.mappings.append(dict(rule, pattern=pattern))
            self._refresh_mappings()
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc), parent=self.win)

    def _refresh_mappings(self):
        self.map_tree.delete(*self.map_tree.get_children())
        for i, rule in enumerate(self.mappings):
            dimension = rule.get('diameter_mm') if rule['category'] == 'pipe' or rule.get('section_shape') == 'round' else f"{rule.get('width_mm', '?')} × {rule.get('height_mm', '?')}"
            self.map_tree.insert('', 'end', iid=str(i), values=(rule['pattern'], rule['category'], rule.get('system', ''), dimension, rule.get('placement', 'source')))

    def _select_mapping(self, _event):
        selected = self.map_tree.selection()
        if selected:
            rule = self.mappings[int(selected[0])]
            for key, var in self.rule_vars.items():
                value = rule.get(key, '')
                var.set(', '.join(value) if isinstance(value, list) else str(value))
            self.source_filter_status.set(f"선택 규칙: {rule['pattern']} / 정확한 블록 인스턴스 출처 {len(rule.get('source_refs', []))}개 유지. 원본 핸들은 원본 해시에 연결됩니다.")

    def _delete_mapping(self):
        removed = set(map(int, self.map_tree.selection()))
        self.mappings = [rule for i, rule in enumerate(self.mappings) if i not in removed]
        self._refresh_mappings()

    def _draw_regions(self):
        if not hasattr(self, 'canvas'):
            return
        self.canvas.delete('all')
        box = self.inventory.get('bounds_mm')
        if not box:
            return
        width, height = max(self.canvas.winfo_width(), 200), 155
        scale = min((width - 40) / max(box[2] - box[0], 1), (height - 30) / max(box[3] - box[1], 1))
        self.region_boxes = []
        for i, region in enumerate(self.regions, 1):
            b = region['bounds_mm']
            rectangle = (20 + (b[0] - box[0]) * scale, 15 + (box[3] - b[3]) * scale,
                         20 + (b[2] - box[0]) * scale, 15 + (box[3] - b[1]) * scale)
            self.region_boxes.append((i, rectangle))
            self.canvas.create_rectangle(*rectangle, outline='#136cbb', width=2, fill='#bde0fe' if self.region_select.current() == i else '#e2e8f0')
            self.canvas.create_text(rectangle[0] + 4, rectangle[1] + 3, text=region['id'], anchor='nw', fill='#16324f')
        self.canvas.create_text(width - 8, height - 5, anchor='se', text='범위 미리보기 · 원본 상세는 3D 미리보기의 DXF 비교 탭', fill='#536477')

    def _pick_region(self, event):
        for index, b in reversed(getattr(self, 'region_boxes', [])):
            if b[0] <= event.x <= b[2] and b[1] <= event.y <= b[3]:
                self.region_select.current(index)
                self._draw_regions()
                break

    def _form_region(self):
        index = self.region_select.current()
        if index < 0:
            raise ValueError('모델링 영역을 선택하세요.')
        if index == len(self.regions) + 1:
            return {'id': self.region_vars['id'].get().strip(),
                    'bounds_mm': [float(self.region_vars[key].get()) for key in ('xmin', 'ymin', 'xmax', 'ymax')]}
        return {key: self.regions[index - 1][key] for key in ('id', 'bounds_mm')} if index > 0 else None

    def _split_by_outline(self):
        """선택 규칙의 중심선을 외곽선 폭으로 재 폭별 규칙으로 나눈다. 단면 형태·높이는 사람이 제품 자료로 채운다."""
        try:
            selected = self.map_tree.selection()
            if len(selected) != 1:
                raise ValueError('나눌 규칙 한 개를 선택하세요.')
            index = int(selected[0])
            row = copy.deepcopy(self.mappings[index])
            from drawing_units import positive_scale
            scale = positive_scale(self.level_vars['unit_scale_to_mm'].get())
            region = self._form_region()
        except ValueError as exc:
            messagebox.showerror('외곽선 폭', str(exc), parent=self.win)
            return
        source = next(s for s in self.manifest['sources'] if s['id'] == self.source_id)
        self.status.set('원본 외곽선 간격을 재고 있습니다…')

        def run():
            try:
                from mep_profile import measure_outline_widths
                result = measure_outline_widths(source['path'], row, scale, region=region and region['bounds_mm'])
                self.win.after(0, lambda: self._split_done(index, row, result))
            except Exception as exc:
                self.win.after(0, lambda message=str(exc): messagebox.showerror('외곽선 폭', message, parent=self.win))
        threading.Thread(target=run, daemon=True).start()

    def _split_done(self, index, row, result):
        from mep_profile import split_rule_by_outline_widths
        if result['source_sha256'] != self.inventory['source_sha256']:
            messagebox.showerror('외곽선 폭', '원본 도면이 바뀌었습니다. 설정 창을 다시 여세요.', parent=self.win)
            return
        reasons = Counter(u['status'] for u in result['unmeasured'])
        lines = [f"폭 {g['width_mm']}mm · {g['count']}개 · {g['length_mm'] / 1000:.1f}m" for g in result['groups']]
        if reasons:
            lines.append('못 잰 원본 ' + ' · '.join(f'{k} {v}' for k, v in reasons.items()) + ' → 별도 규칙')
        self.status.set('외곽선 폭: ' + ' / '.join(lines))
        if not result['groups']:
            messagebox.showinfo('외곽선 폭', '잴 수 있는 외곽선이 없습니다.\n' + '\n'.join(lines), parent=self.win)
            return
        if not messagebox.askyesno('외곽선 폭', '\n'.join(lines) + '\n\n이 규칙을 폭별로 나눌까요? 평면에는 높이와 원형/사각 구분이 없습니다 — '
                                   '나눈 규칙마다 제품 자료로 단면 형태·높이를 확인해 입력하세요.', parent=self.win):
            return
        self.mappings[index:index + 1] = split_rule_by_outline_widths(row, result)
        self._refresh_mappings()

    def _pick_equipment_body(self):
        """겹친 기호에서 본체 한 겹만 남기는 원본 필터를 **제안**받는다. 고르는 것은 사람이다."""
        try:
            selected = self.map_tree.selection()
            if len(selected) != 1:
                raise ValueError('장비 규칙 한 개를 선택하세요.')
            index = int(selected[0])
            row = copy.deepcopy(self.mappings[index])
            if row.get('representation') != 'outline' or row.get('category') != 'equipment':
                raise ValueError('장비 외곽선(equipment + outline) 규칙에만 쓸 수 있습니다.')
            from drawing_units import positive_scale
            scale = positive_scale(self.level_vars['unit_scale_to_mm'].get())
            region = self._form_region()
        except ValueError as exc:
            messagebox.showerror('장비 본체', str(exc), parent=self.win)
            return
        source = next(s for s in self.manifest['sources'] if s['id'] == self.source_id)
        self.status.set('기호의 닫힌 면을 세고 있습니다…')

        def run():
            try:
                from mep_profile import measure_equipment_bodies
                result = measure_equipment_bodies(source['path'], row, scale, region=region and region['bounds_mm'])
                self.win.after(0, lambda: self._equipment_body_done(index, row, result))
            except Exception as exc:
                self.win.after(0, lambda message=str(exc): messagebox.showerror('장비 본체', message, parent=self.win))
        threading.Thread(target=run, daemon=True).start()

    def _equipment_body_done(self, index, row, result):
        from mep_profile import split_rule_by_equipment_bodies
        if result['source_sha256'] != self.inventory['source_sha256']:
            messagebox.showerror('장비 본체', '원본 도면이 바뀌었습니다. 설정 창을 다시 여세요.', parent=self.win)
            return
        faces = Counter(len(i['faces']) for i in result['instances'])
        lines = [f"기호 {len(result['instances'])}곳 · 겹 수 " + ' · '.join(f'{k}겹 {v}곳' for k, v in sorted(faces.items()))]
        rule, _status = split_rule_by_equipment_bodies(row, result)
        if rule is None:
            reasons = Counter(i['reason'] for i in result['ambiguous_instances'])
            lines.append('제안할 수 없음: ' + ' · '.join(f'{k} {v}곳' for k, v in reasons.items()))
            for entry in result['ambiguous_instances'][:3]:
                lines.append('  면 ' + ' / '.join(f"{f['area_mm2']:.0f}mm² 핸들 {','.join(f['handles'][:3])}"
                                                  for f in entry['faces'][:4]))
            self.status.set('장비 본체: ' + lines[1])
            messagebox.showinfo('장비 본체', '\n'.join(lines)
                                + '\n\n도면에서 본체 한 겹을 골라 원본 필터에 직접 적으세요.',
                                parent=self.win)
            return
        picked = result['suggestion']
        lines.append('제안: ' + ('핸들 ' + ', '.join(picked['source_handles']) if 'source_handles' in picked
                                 else f"원본 {len(picked['source_refs'])}개") + f" · 기호 {picked['instances']}곳")
        self.status.set('장비 본체: ' + lines[-1])
        if not messagebox.askyesno('장비 본체', '\n'.join(lines)
                                   + '\n\n이 규칙에 그 필터를 적용할까요? '
                                   '기호 바깥에 점검 여유선을 둔 도면이면 가장 바깥 면이 본체가 아닐 수 있습니다 — '
                                   '도면에서 확인하세요.', parent=self.win):
            return
        self.mappings[index] = rule
        self._refresh_mappings()

    def _form_profile(self):
        if not self.mappings and not self.architecture_rules:
            raise ValueError('모델링할 레이어를 최소 한 개 설정하세요.')
        values = {key: float(var.get()) for key, var in self.level_vars.items() if var.get().strip()}
        if values.get('unit_scale_to_mm') != self.inventory.get('scale_to_mm'):
            raise ValueError('단위가 변경되었습니다. [입력 단위로 영역 다시 분석] 후 영역을 다시 선택하세요.')
        profile = {'version': 1, 'source_sha256': self.inventory['source_sha256'], 'layers': copy.deepcopy(self.mappings),
                   'levels': {key: values[key] for key in ('structural_slab_top_mm', 'floor_to_floor_mm', 'slab_thickness_mm') if key in values},
                   'floor_layers': [{'role': key, 'thickness_mm': values[key]} for key in ('impact_insulation', 'foamed_concrete', 'screed') if key in values]}
        if self.architecture_rules:
            profile['architecture_layers'] = copy.deepcopy(self.architecture_rules)
        for key in ('unit_scale_to_mm', 'curve_chord_error_mm', 'endpoint_tolerance_mm', 'gap_review_mm'):
            if key in values:
                profile[key] = values[key]
        region = self._form_region()
        if region:
            profile['region'] = region
        return profile

    def _save(self):
        try:
            profile = self._form_profile()
            from mep_profile import validate_profile
            profile = validate_profile(profile, source_sha256=self.inventory['source_sha256'])
        except (ValueError, KeyError) as exc:
            messagebox.showerror('설정 확인', str(exc), parent=self.win)
            return
        self.save_button.state(['disabled'])
        self.status.set('설정을 검증하고 후보 모델을 생성하고 있습니다…')
        def run():
            try:
                manifest = self.manifest
                if self.proposal and profile == self.proposal['profile']:
                    state = self.session.apply_mep_proposal(self.proposal['proposal_id'], manifest['revision'], manifest['project_id'])
                else:
                    state = self.session.configure_source(profile, manifest['revision'], manifest['project_id'], self.source_id)
                self.win.after(0, lambda: self._saved(state))
            except Exception as exc:
                self.win.after(0, lambda message=str(exc): self._failed(message))
        threading.Thread(target=run, daemon=True).start()

    def _saved(self, state):
        self.manifest = self.session.store.read()
        self.proposal = None
        self.save_button.state(['!disabled'])
        # 실무 도면의 설비 해석을 방금 정했다 — 회귀로 잠그지 않으면 다음 변경이 조용히 바꾼다.
        self.status.set(f"프로젝트 revision {state['revision']} 저장 완료. 원본 비교·검토 진단을 확인하세요. "
                        "· 회귀 등록: tests/golden.local.json 에 이 프로젝트 폴더를 중립적인 이름으로 적고 "
                        "python tests/test_golden.py --bless (docs/release_checklist.md)")
        self._refresh_proposals()
        self.on_saved(state)

    def _failed(self, message):
        self.save_button.state(['!disabled'])
        self.status.set('저장하지 못했습니다. 기존 프로젝트는 유지됩니다.')
        messagebox.showerror('설정 저장 실패', message + '\n다른 창에서 변경되었다면 닫고 설정 창을 다시 여세요.', parent=self.win)
