"""GUI form for source-bound MEP profiles. Codex proposals use the same form and save gate."""
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
    def __init__(self, parent, session, inventory, on_saved):
        self.session, self.inventory, self.on_saved = session, inventory, on_saved
        self.manifest = session.store.refresh_inputs()
        self.source_id = self.manifest['sources'][0]['id']
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
        self.status = tk.StringVar(value='원본 SHA-256: ' + inventory['source_sha256'][:20])
        ttk.Label(self.win, textvariable=self.status, wraplength=1050).pack(anchor='w', padx=12, pady=6)
        controls = ttk.Frame(self.win)
        controls.pack(fill='x', padx=10, pady=8)
        self.save_button = ttk.Button(controls, text='설정 저장 · 다시 모델링', command=self._save)
        self.save_button.pack(side='right', padx=5)
        ttk.Button(controls, text='닫기', command=self.win.destroy).pack(side='right')
        current = self.manifest['sources'][0].get('options', {}).get('mep_profile') or {}
        self._load_profile(current)
        self._draw_regions()

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
            ('category', '형상', ['pipe', 'duct', 'tray']), ('system', '계통 / 회로', ['heating', 'SA', 'RA', 'OA', 'EA']),
            ('representation', '원본 표현', ['centerline', 'outline']), ('diameter_mm', '실외경 mm (배관)', None),
            ('width_mm', '폭 mm (덕트)', None), ('height_mm', '높이 mm (덕트)', None),
            ('nominal_size', '공칭 규격 (예: 15A)', None), ('material', '재질', ['PB', 'PVC', 'steel']),
            ('placement', '설치 기준', ['source', 'center', 'slab_soffit', 'foam_top']),
            ('center_elevation_mm', '중심 높이 mm', None), ('dimension_basis', '치수 근거', ['user', 'assumed', 'annotation']),
            ('color', '색상 필터 (선택)', None), ('linetype', '선종류 필터 (선택)', None)]):
            self.rule_vars[key] = self._entry(form, label, i, values)
        for key, value in {'category': 'pipe', 'system': 'heating', 'representation': 'centerline', 'material': 'PB', 'placement': 'foam_top', 'dimension_basis': 'assumed'}.items():
            self.rule_vars[key].set(value)
        ttk.Button(form, text='선택 레이어에 설정 추가', command=self._add_mapping).grid(row=13, column=0, columnspan=2, pady=5)
        self.map_tree = ttk.Treeview(left, columns=('pattern', 'category', 'system', 'dimension', 'placement'), show='headings', height=5)
        for key, label, width in [('pattern', '저장할 규칙', 250), ('category', '형상', 55), ('system', '계통', 65), ('dimension', '치수 mm', 85), ('placement', '설치', 105)]:
            self.map_tree.heading(key, text=label)
            self.map_tree.column(key, width=width, stretch=key == 'pattern')
        ttk.Label(left, text='이번 프로젝트의 매핑 규칙').pack(anchor='w', pady=(7, 0))
        self.map_tree.pack(fill='x')
        self.map_tree.bind('<<TreeviewSelect>>', self._select_mapping)
        ttk.Button(left, text='선택 규칙 삭제', command=self._delete_mapping).pack(anchor='e', pady=3)

    def _build_levels(self):
        form = ttk.Frame(self.level_tab)
        form.pack(anchor='nw', padx=18, pady=12)
        ttk.Label(form, text='모든 높이는 바닥 구조 슬라브 윗면 기준입니다. 원본의 설치 높이가 없으면 입력값이 모델링 가정으로 기록됩니다.', wraplength=950).grid(row=0, column=0, columnspan=3, sticky='w', pady=8)
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
            scale = float(self.level_vars['unit_scale_to_mm'].get())
            if scale <= 0:
                raise ValueError('단위 배율은 0보다 커야 합니다.')
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

    def _add_mapping(self):
        try:
            rule = {key: value.get().strip() for key, value in self.rule_vars.items() if value.get().strip()}
            for key in ('diameter_mm', 'width_mm', 'height_mm', 'center_elevation_mm'):
                if key in rule:
                    rule[key] = float(rule[key])
            if 'color' in rule:
                rule['color'] = int(rule['color'])
            selected = self.layer_tree.selection()
            if not selected:
                raise ValueError('원본 레이어를 먼저 선택하세요.')
            for index in selected:
                pattern = '^' + re.escape(self.layers[int(index)]['name']) + '$'
                self.mappings = [m for m in self.mappings if not (m['pattern'] == pattern and m.get('color') == rule.get('color') and m.get('linetype') == rule.get('linetype'))]
                self.mappings.append(dict(rule, pattern=pattern))
            self._refresh_mappings()
        except ValueError as exc:
            messagebox.showerror('설정 확인', str(exc), parent=self.win)

    def _refresh_mappings(self):
        self.map_tree.delete(*self.map_tree.get_children())
        for i, rule in enumerate(self.mappings):
            dimension = rule.get('diameter_mm') if rule['category'] == 'pipe' else f"{rule.get('width_mm', '?')} × {rule.get('height_mm', '?')}"
            self.map_tree.insert('', 'end', iid=str(i), values=(rule['pattern'], rule['category'], rule.get('system', ''), dimension, rule.get('placement', 'source')))

    def _select_mapping(self, _event):
        selected = self.map_tree.selection()
        if selected:
            rule = self.mappings[int(selected[0])]
            for key, var in self.rule_vars.items():
                var.set(str(rule.get(key, '')))

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

    def _form_profile(self):
        if not self.mappings:
            raise ValueError('모델링할 레이어를 최소 한 개 설정하세요.')
        values = {key: float(var.get()) for key, var in self.level_vars.items() if var.get().strip()}
        if values.get('unit_scale_to_mm') != self.inventory.get('scale_to_mm'):
            raise ValueError('단위가 변경되었습니다. [입력 단위로 영역 다시 분석] 후 영역을 다시 선택하세요.')
        profile = {'version': 1, 'source_sha256': self.inventory['source_sha256'], 'layers': copy.deepcopy(self.mappings),
                   'levels': {key: values[key] for key in ('structural_slab_top_mm', 'floor_to_floor_mm', 'slab_thickness_mm') if key in values},
                   'floor_layers': [{'role': key, 'thickness_mm': values[key]} for key in ('impact_insulation', 'foamed_concrete', 'screed') if key in values]}
        for key in ('unit_scale_to_mm', 'curve_chord_error_mm', 'endpoint_tolerance_mm', 'gap_review_mm'):
            if key in values:
                profile[key] = values[key]
        index = self.region_select.current()
        if index < 0:
            raise ValueError('모델링 영역을 선택하세요.')
        if index == len(self.regions) + 1:
            profile['region'] = {'id': self.region_vars['id'].get().strip(),
                'bounds_mm': [float(self.region_vars[key].get()) for key in ('xmin', 'ymin', 'xmax', 'ymax')]}
        elif index > 0:
            profile['region'] = {key: self.regions[index - 1][key] for key in ('id', 'bounds_mm')}
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
        self.status.set(f"프로젝트 revision {state['revision']} 저장 완료. 원본 비교·검토 진단을 확인하세요.")
        self._refresh_proposals()
        self.on_saved(state)

    def _failed(self, message):
        self.save_button.state(['!disabled'])
        self.status.set('저장하지 못했습니다. 기존 프로젝트는 유지됩니다.')
        messagebox.showerror('설정 저장 실패', message + '\n다른 창에서 변경되었다면 닫고 설정 창을 다시 여세요.', parent=self.win)
