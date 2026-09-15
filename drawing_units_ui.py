"""Desktop unit review for both architecture and MEP; all writes use ProjectSession."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from drawing_units import positive_scale


def unit_summary(inventory):
    review = inventory.get('unit_review', {})
    header = review.get('header_scale_to_mm')
    scale = inventory.get('scale_to_mm')
    return (f"DXF 헤더: {review.get('header_unit', '미확인')} · 1단위 = {header:g} mm" if header is not None else
            'DXF 헤더 단위 미지정 · 실제 치수와 비교해 배율을 입력하세요.') + (
            f" / 현재 분석: 1단위 = {scale:g} mm" if scale is not None else '') + (
            ' / 헤더와 다른 저장값 사용' if review.get('header_differs') else '') + (
            ' / 기존 프로젝트 배율 유지: 변경하려면 치수를 비교한 뒤 저장하세요.'
            if review.get('basis') == 'legacy_header_policy' else '')


class UnitSetupDialog:
    def __init__(self, parent, session, inventory, on_saved, source_id=None):
        self.session, self.inventory, self.on_saved = session, inventory, on_saved
        self.manifest = session.store.refresh_inputs()
        self.source_id = source_id or self.manifest['sources'][0]['id']
        self.win = tk.Toplevel(parent)
        self.win.title('도면 단위 확인 · 프로젝트에 저장')
        self.win.geometry('850x650')
        self.win.minsize(740, 580)
        self.summary = tk.StringVar(value=unit_summary(inventory))
        ttk.Label(self.win, textvariable=self.summary, wraplength=790).pack(anchor='w', padx=16, pady=12)
        ttk.Label(self.win, text='헤더가 실제 작성 단위와 다를 수 있습니다. 주석과 알고 있는 치수를 비교해 결정하세요.\n주석의 숫자만으로 단위를 자동 확정하지 않습니다.', wraplength=790).pack(anchor='w', padx=16)
        form = ttk.Frame(self.win); form.pack(fill='x', padx=16, pady=12)
        self.scale = tk.StringVar(value=str(inventory.get('scale_to_mm') or ''))
        self.reference_raw, self.reference_mm = tk.StringVar(), tk.StringVar()
        ttk.Label(form, text='도면 1단위 → mm').grid(row=0, column=0, sticky='w', pady=6)
        scale_entry = ttk.Combobox(form, textvariable=self.scale, values=['1', '10', '1000', '25.4', '304.8'], width=20)
        scale_entry.grid(row=0, column=1, sticky='w')
        ttk.Label(form, text='1=mm · 10=cm · 1000=m · 25.4=inch · 304.8=ft').grid(row=0, column=2, padx=8)
        ttk.Label(form, text='비교할 원본 길이').grid(row=1, column=0, sticky='w')
        raw_entry = ttk.Entry(form, textvariable=self.reference_raw, width=22); raw_entry.grid(row=1, column=1)
        ttk.Label(form, text='알고 있는 실제 길이 mm').grid(row=2, column=0, sticky='w')
        mm_entry = ttk.Entry(form, textvariable=self.reference_mm, width=22); mm_entry.grid(row=2, column=1)
        self.compare_button = ttk.Button(form, text='두 길이로 배율 계산', command=self._compare)
        self.compare_button.grid(row=2, column=2, sticky='w', padx=8)
        self.comparison = tk.StringVar()
        ttk.Label(self.win, textvariable=self.comparison, wraplength=790).pack(anchor='w', padx=16, pady=4)
        evidence = tk.Text(self.win, height=12, wrap='word')
        evidence.pack(fill='both', expand=True, padx=16, pady=6)
        lines = ['비교 근거 샘플 (모든 도형을 나열한 목록은 아닙니다)']
        for row in inventory.get('unit_evidence', {}).get('measurements', []):
            ref = row['source_refs'][0]
            lines.append(f"선 {ref['handle']} · {row['layer']} · 원본 길이 {row['raw_length']:.6g}")
        for row in inventory.get('unit_evidence', {}).get('annotations', []):
            lines.append(f"주석 {row['source_refs'][0]['handle']} · {row['layer']} · {row['text']}")
        evidence.insert('1.0', '\n'.join(lines)); evidence.configure(state='disabled')
        ttk.Label(self.win, text='저장 시 같은 원본 영역을 유지하도록 영역 좌표를 환산합니다. mm로 입력한 단면·높이·수정값은 유지하며, 수정 연결과 검토 상태는 다시 확인합니다.', wraplength=790).pack(anchor='w', padx=16, pady=5)
        self.status = tk.StringVar(value='배율을 변경한 뒤 저장해야 모델과 원본 겹쳐보기에 반영됩니다.')
        ttk.Label(self.win, textvariable=self.status, wraplength=790).pack(anchor='w', padx=16, pady=5)
        self.save_button = ttk.Button(self.win, text='단위 저장 · 다시 모델링', command=self._save)
        self.save_button.pack(anchor='e', padx=16, pady=10)
        self.controls = [scale_entry, raw_entry, mm_entry, self.compare_button, self.save_button]
        self.busy = False
        self.scale.trace_add('write', lambda *_: self._update_comparison())
        self._update_comparison()

    def _update_comparison(self):
        try:
            scale = positive_scale(self.scale.get())
        except ValueError:
            self.comparison.set('유한한 양수 배율을 입력하세요.'); return
        review = self.inventory.get('unit_review', {})
        header = review.get('header_scale_to_mm')
        rows = self.inventory.get('unit_evidence', {}).get('measurements', [])
        parts = [f'입력 배율: {scale:g} mm/단위 (저장 전)']
        if header is not None and abs(scale - header) > max(scale, header) * 1e-9:
            parts.append(f'헤더 {header:g}과 다릅니다')
        if rows:
            parts.append(f"샘플 선: {rows[0]['raw_length']:g} → {rows[0]['raw_length'] * scale:g} mm")
        bounds = self.inventory.get('bounds_mm'); old = self.inventory.get('scale_to_mm')
        if bounds and old:
            parts.append(f'도면 전체 범위: {(bounds[2]-bounds[0])*scale/old:g} × {(bounds[3]-bounds[1])*scale/old:g} mm')
        self.comparison.set(' / '.join(parts))

    def _compare(self):
        try:
            scale = positive_scale(self.reference_mm.get()) / positive_scale(self.reference_raw.get())
            self.scale.set(format(positive_scale(scale), '.15g'))
        except ValueError as exc:
            messagebox.showerror('비교 치수 확인', str(exc), parent=self.win)

    def _save(self):
        if self.busy:
            return
        try:
            scale = positive_scale(self.scale.get())
        except ValueError as exc:
            messagebox.showerror('단위 확인', str(exc), parent=self.win); return
        self.busy = True
        for control in self.controls:
            control.state(['disabled'])
        self.status.set('단위를 저장하고 모델을 다시 분석합니다…')
        results = queue.SimpleQueue()
        def run():
            try:
                results.put((True, self.session.configure_units(scale, self.manifest['revision'],
                    self.manifest['project_id'], self.source_id, source_sha256=self.inventory['source_sha256'])))
            except Exception as exc:
                results.put((False, str(exc)))
        def poll():
            try:
                ok, result = results.get_nowait()
            except queue.Empty:
                self.win.after(50, poll); return
            self.busy = False
            for control in self.controls:
                control.state(['!disabled'])
            if not ok:
                self.status.set('저장 실패 · 기존 설정 유지. 원본이나 revision이 변경되면 창을 다시 여세요.')
                messagebox.showerror('단위 저장 실패', result, parent=self.win); return
            self.manifest = self.session.store.read()
            header = self.inventory.get('unit_review', {}).get('header_unit', '미확인')
            self.summary.set(f'저장된 배율: 1단위 = {scale:g} mm / DXF 헤더: {header}')
            count = len(result['geometry'].get('edits_report', {}).get('orphaned', []))
            self.status.set(f"revision {result['revision']} 저장 완료 · 수정 재연결 검토 {count}건. 원본 비교와 검토 목록을 확인하세요.")
            self.on_saved(result)
        threading.Thread(target=run, daemon=True).start()
        self.win.after(50, poll)
