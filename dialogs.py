"""The smaller windows of the Shadow app: YouTube... (YouTubeDialog), Narration... (NarrationDialog) and the Check frames
result (CheckWindow). Split out of app.py (3.16); app.py imports them back (app.YouTubeDialog etc.).
They only use the App instance they are given and the helpers below - nothing from app.py's module state."""
import logging, os, threading, webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import blender_core as bc
from scene_paths import scene_for_mp4
from shadow_util import dark_titlebar, narrate_module, open_path
log = logging.getLogger('shadow')

class YouTubeDialog:
    """YouTube...: upload rendered videos to a signed-in channel (youtube.py, YouTube Data API, your own OAuth client).
    Titles / descriptions / tags / Made for Kids come from metadata.txt next to the video or next to its scene ('=== SHORT ===' section
    for *_shorts.mp4). Privacy defaults to Private. Until the Google Cloud project passes YouTube's audit, YouTube keeps every
    API upload Private whatever is chosen. upload() runs in a thread; tests call add()/start() with youtube.py patched."""
    def __init__(self, app, items):
        import youtube as ytm
        self.app, self.ytm, self.items, self.cancel, self.busy, self.last = app, ytm, [], threading.Event(), False, None
        t = self.top = tk.Toplevel(app); t.title('Upload to YouTube'); t.transient(app); t.minsize(640, 360)
        f = ttk.Frame(t, padding=14); f.pack(fill='both', expand=True)
        ttk.Label(f, text='Channel').grid(row=0, column=0, sticky='w')
        self.channel = tk.StringVar(); self.ch_box = ttk.Combobox(f, textvariable=self.channel, state='readonly', width=34)
        self.ch_box.grid(row=0, column=1, sticky='w', padx=8)
        ttk.Button(f, text='Sign in...', command=self.sign_in).grid(row=0, column=2, sticky='w')
        self.tree = ttk.Treeview(f, columns=('title', 'when', 'status'), height=6)
        for c, w, txt in (('#0', 200, 'Video'), ('title', 240, 'Title (metadata.txt)'), ('when', 120, 'Publish'), ('status', 170, 'Status')):
            self.tree.heading(c, text=txt); self.tree.column(c, width=w, stretch=c != 'status')
        self.tree.grid(row=1, column=0, columnspan=4, sticky='nsew', pady=10)
        self.tree.bind('<Double-1>', lambda e: self.open_selected())
        self.tree.bind('<<TreeviewSelect>>', lambda e: self.show_source())
        self.ask = lambda title, text: messagebox.askyesno(title, text, parent=self.top)   # tests replace it
        ttk.Label(f, text='Privacy').grid(row=2, column=0, sticky='w')
        self.privacy = tk.StringVar(value='private')
        ttk.Combobox(f, textvariable=self.privacy, values=list(ytm.PRIVACY), state='readonly', width=12).grid(row=2, column=1, sticky='w', padx=8)
        self.kids = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text='Made for kids', variable=self.kids).grid(row=2, column=2, sticky='w')
        self.ai, self.thumb, self.sched = tk.BooleanVar(value=False), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        opt = ttk.Frame(f); opt.grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        ttk.Checkbutton(opt, text='Altered or synthetic (AI) content', variable=self.ai).pack(side='left')
        ttk.Checkbutton(opt, text='Set thumbnail (*_thumb.jpg)', variable=self.thumb).pack(side='left', padx=12)
        ttk.Checkbutton(opt, text='Schedule (PUBLISH: time)', variable=self.sched).pack(side='left')
        ttk.Label(f, text='Until Google approves the app (YouTube API audit), YouTube keeps API uploads Private.',
                  foreground='#888').grid(row=4, column=0, columnspan=4, sticky='w', pady=(6, 0))
        self.bar = ttk.Progressbar(f, maximum=100); self.bar.grid(row=5, column=0, columnspan=4, sticky='we', pady=(10, 0))
        self.msg = ttk.Label(f, text='', foreground='#888'); self.msg.grid(row=6, column=0, columnspan=4, sticky='w')
        bt = ttk.Frame(f); bt.grid(row=7, column=0, columnspan=4, sticky='we', pady=(10, 0))
        ttk.Button(bt, text='Add video...', command=self.pick).pack(side='left')
        ttk.Button(bt, text='Remove', command=self.remove).pack(side='left', padx=6)
        self.studio_btn = ttk.Button(bt, text='Open in Studio', command=self.open_selected, state='disabled'); self.studio_btn.pack(side='left')
        ttk.Button(bt, text='Close', command=self.close).pack(side='right')
        self.up_btn = ttk.Button(bt, text='Upload', style='Accent.TButton', command=self.start); self.up_btn.pack(side='right', padx=6)
        f.columnconfigure(3, weight=1); f.rowconfigure(1, weight=1)
        t.protocol('WM_DELETE_WINDOW', self.close); t.bind('<Escape>', lambda e: self.close())
        dark_titlebar(t, app.theme == 'dark')
        self.refresh_channels()
        for mp4, scene in items: self.add(mp4, scene)
        if not self.items: self.msg.config(text='Render first, or "Add video..." to pick mp4 files.')

    def refresh_channels(self, select=None):
        ch = self.ytm.channels(); names = [c['title'] for c in ch]
        self.ch_box.config(values=names)
        if select: self.channel.set(select)
        elif self.channel.get() not in names: self.channel.set(names[0] if len(names) == 1 else '')
        try: self.ytm.load_client()
        except self.ytm.YouTubeError as e: self.msg.config(text=str(e))

    def add(self, mp4, scene=None):
        """One video row. Its metadata.txt is looked for next to the video, then next to its scene: the row's scene, or for
        "Add video..." the queued scene that makes this file (scene_for_mp4), so a picked mp4 still gets its metadata."""
        mp4 = os.path.normpath(mp4)   # the file picker gives C:/... - the same file must not be listed twice
        if any(os.path.normcase(i['mp4']) == os.path.normcase(mp4) for i in self.items): return
        scene = scene or scene_for_mp4(mp4, getattr(self.app, 'files', []))
        it = dict(mp4=mp4, scene=scene, cands=[os.path.dirname(scene)] if scene else [], meta=None, src=None, result=None)
        it['iid'] = self.tree.insert('', 'end', text=os.path.basename(mp4), values=('', '', 'Ready'))
        self.items.append(it); self.load_meta(it)
        meta = it['meta']
        if meta and meta.get('kids') is not None and len(self.items) == 1: self.kids.set(bool(meta['kids']))
        if meta and meta.get('ai') is not None and len(self.items) == 1: self.ai.set(bool(meta['ai']))

    def load_meta(self, it):
        """(Re)read the row's metadata.txt from disk (also right before Upload: a file written after the dialog opened counts)."""
        it['meta'], it['src'] = self.ytm.metadata_for(it['mp4'], it['cands'])
        m = it['meta'] or {}
        self.tree.set(it['iid'], 'title', m.get('title') or '(no metadata.txt: file name)'); self.tree.set(it['iid'], 'when', self.when_text(m))

    def show_source(self):
        """Status line for the selected row: which metadata.txt its upload will use."""
        sel = set(self.tree.selection()); it = next((i for i in self.items if i['iid'] in sel), None)
        if it: self.msg.config(text=f"metadata: {it['src']}" if it['src'] else 'no metadata.txt next to this video'
                               + (f" or its scene ({os.path.basename(it['scene'])})" if it['scene'] else ' (and no queued scene makes it)'))

    @staticmethod
    def when_text(meta):
        """Publish column: the PUBLISH: time in this PC's zone, '' without PUBLISH:, '(not a date)' when it has no date + time."""
        m = meta or {}
        if m.get('publish_at'):
            import datetime
            t = datetime.datetime.strptime(m['publish_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).astimezone()
            return t.strftime('%a %d %b %H:%M')
        return '(not a date)' if m.get('publish') else ''

    def pick(self):
        for p in filedialog.askopenfilenames(parent=self.top, filetypes=[('MP4 video', '*.mp4')], initialdir=self.app.out_dir.get()): self.add(p)

    def remove(self):
        if self.busy: return
        sel = set(self.tree.selection())
        for it in [i for i in self.items if i['iid'] in sel]: self.tree.delete(it['iid']); self.items.remove(it)

    def sign_in(self):
        if self.busy: return
        self.msg.config(text='Sign in on the Google page that opened in your browser...')
        def work():
            try: ch = self.ytm.sign_in(); self.top.after(0, lambda: (self.refresh_channels(ch['title']), self.msg.config(text=f"Signed in: {ch['title']}")))
            except Exception as e: self.top.after(0, lambda e=e: self.msg.config(text=f'Sign-in failed: {e}'))
        threading.Thread(target=work, daemon=True).start()

    def start(self, wait=False):
        """Upload every video that is not uploaded yet. wait=True (tests) runs in this thread."""
        if self.busy:
            self.cancel.set(); self.msg.config(text='Stopping after the current chunk...'); return
        todo = [i for i in self.items if not i['result']]
        if not todo: self.msg.config(text='Nothing to upload.'); return
        if not self.channel.get(): self.msg.config(text='Choose a channel (or Sign in...).'); return
        for it in todo: self.load_meta(it)   # what is on disk now, not what was there when the dialog opened
        bare = [os.path.basename(i['mp4']) for i in todo if not i['meta']]
        if bare and not self.ask('YouTube', 'No metadata.txt found for:\n\n' + '\n'.join(bare) +
                                 '\n\nThey would be uploaded with the file name as title and no description, tags or schedule. Upload them anyway?'):
            self.msg.config(text='Put metadata.txt next to the video or its scene, then press Upload.'); return
        self.busy = True; self.cancel.clear(); self.up_btn.config(text='Stop'); ch = self.channel.get()
        privacy, kids, ai, thumb, sched = self.privacy.get(), self.kids.get(), self.ai.get(), self.thumb.get(), self.sched.get()
        bad_thumb = []
        def set_row(it, status): self.top.after(0, lambda: self.tree.set(it['iid'], 'status', status))
        def work():
            ok = 0
            for n, it in enumerate(todo):
                set_row(it, 'Uploading 0%')
                def prog(d, t, it=it, n=n):
                    pct = d * 100 // max(t, 1); set_row(it, f'Uploading {pct}%')
                    self.top.after(0, lambda: self.bar.config(value=(n + d / max(t, 1)) * 100 / len(todo)))
                try:
                    r = self.ytm.upload_video(it['mp4'], ch, it['meta'] or {}, privacy, progress=prog, cancel=self.cancel,
                                              overrides=dict(kids=kids, privacy=privacy, ai=ai), meta_path=it['src'],
                                              thumbnail=thumb, schedule=sched)
                    it['result'] = r; self.last = r; ok += 1
                    extra = (['scheduled'] if r.get('publish_at') else []) + (['thumbnail'] if r.get('thumb') == 'set' else [])
                    if str(r.get('thumb') or '').startswith('error'): extra.append('no thumbnail'); bad_thumb.append(r['thumb'][7:])
                    set_row(it, f"Done ({', '.join([r.get('privacy') or privacy] + extra)})")
                    log.info('youtube upload %s -> %s %s thumb=%s publish=%s', it['mp4'], r['channel'], r['url'], r.get('thumb'), r.get('publish_at'))
                except Exception as e:
                    set_row(it, 'Stopped' if self.cancel.is_set() else 'Error'); log.warning('youtube upload %s: %s', it['mp4'], e)
                    self.top.after(0, lambda e=e: self.msg.config(text=str(e)))
                    if self.cancel.is_set(): break
            def end():
                self.busy = False; self.up_btn.config(text='Upload')
                if ok:
                    self.studio_btn.config(state='normal')
                    self.msg.config(text=f'{ok} uploaded to {ch}. Double-click a row to open it in YouTube Studio.'
                                    + (f' Thumbnail not set: {bad_thumb[-1]}' if bad_thumb else ''))
            self.top.after(0, end)
        if wait: work()
        else: threading.Thread(target=work, daemon=True).start()

    def open_selected(self):
        sel = set(self.tree.selection())
        it = next((i for i in self.items if i['iid'] in sel and i['result']), None) or (self.last and {'result': self.last})
        if it: webbrowser.open(it['result']['studio'])

    def close(self):
        if self.busy and not messagebox.askyesno('YouTube', 'An upload is running. Stop it and close?', parent=self.top): return
        self.cancel.set(); self.top.destroy()

class NarrationDialog:
    """Small dialog for one queue row: narration WAV, words.json, style, captions; OK / Remove narration / Cancel.
    Plain ttk widgets, so it follows the app's sv-ttk dark/light theme; the title bar follows it too.
    ok() / remove() / cancel() are the button handlers (tests call them directly)."""
    def __init__(self, app, path):
        self.app, self.path = app, path
        cur = app.narr.get(path) or {}
        base = os.path.splitext(path)[0]
        guess = lambda ext: base + ext if os.path.isfile(base + ext) else ''
        self.wav = tk.StringVar(value=cur.get('wav') or guess('.wav'))
        w = cur.get('words'); self.words = tk.StringVar(value=w if isinstance(w, str) else guess('.words.json'))
        self.style = tk.StringVar(value=cur.get('style', 'pop'))
        self.captions = tk.BooleanVar(value=cur.get('captions', True))
        t = self.top = tk.Toplevel(app); t.title(f'Narration - {os.path.basename(path)}'); t.transient(app); t.resizable(False, False)
        f = ttk.Frame(t, padding=14); f.pack(fill='both', expand=True)
        for r, (label, var, pick) in enumerate((('Voice (WAV)', self.wav, self.pick_wav), ('Words (words.json)', self.words, self.pick_words))):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', pady=4)
            ttk.Entry(f, textvariable=var, width=46).grid(row=r, column=1, sticky='we', padx=8, pady=4)
            ttk.Button(f, text='Browse...', command=pick).grid(row=r, column=2, pady=4)
        ttk.Label(f, text='Caption style').grid(row=2, column=0, sticky='w', pady=4)
        ttk.Combobox(f, textvariable=self.style, values=list(narrate_module().STYLES), state='readonly', width=12).grid(row=2, column=1, sticky='w', padx=8, pady=4)
        ttk.Checkbutton(f, text='Word captions (2D scenes; WebGL scenes get the voice only)', variable=self.captions).grid(row=3, column=1, columnspan=2, sticky='w', padx=8, pady=4)
        bt = ttk.Frame(f); bt.grid(row=4, column=0, columnspan=3, sticky='e', pady=(10, 0))
        ttk.Button(bt, text='OK', style='Accent.TButton', command=self.ok).pack(side='left')
        ttk.Button(bt, text='Remove narration', command=self.remove, state='normal' if path in app.narr else 'disabled').pack(side='left', padx=6)
        ttk.Button(bt, text='Cancel', command=self.cancel).pack(side='left')
        t.protocol('WM_DELETE_WINDOW', self.cancel); t.bind('<Escape>', lambda e: self.cancel())
        dark_titlebar(t, app.theme == 'dark')
        try: t.grab_set()
        except tk.TclError: pass   # the main window may be hidden (tests)
    def pick_wav(self):
        p = filedialog.askopenfilename(parent=self.top, filetypes=[('WAV', '*.wav'), ('Audio', '*.wav *.mp3 *.m4a *.flac *.ogg')])
        if p: self.wav.set(p)
    def pick_words(self):
        p = filedialog.askopenfilename(parent=self.top, filetypes=[('Word timings', '*.json')])
        if p: self.words.set(p)
    def ok(self):
        err = self.app.set_narration(self.path, self.wav.get().strip(), self.words.get().strip(), style=self.style.get(), captions=self.captions.get())
        if err:
            messagebox.showerror('Narration', err, parent=self.top); return err   # stays open to fix it
        self.app.status.config(text=f'{os.path.basename(self.path)}: narration set ({self.style.get()})'); self.close(); return None
    def remove(self):
        self.app.clear_narration(self.path); self.app.status.config(text=f'{os.path.basename(self.path)}: narration removed'); self.close()
    def cancel(self): self.close()
    def close(self):
        try: self.top.grab_release()
        except tk.TclError: pass
        self.top.destroy(); self.app._update_narr_btn()

class CheckWindow:
    """Result of 'Check frames' for one scene: the contact sheet (scaled to fit), OK or the problems (page errors, impure
    seek(t), never ready...), notes, and Open image / Close. Follows the app's theme."""
    def __init__(self, app, path, res):
        self.app, self.path, self.res = app, path, res
        t = self.top = tk.Toplevel(app); t.title(f'Frame check - {os.path.basename(path)}'); t.transient(app)
        f = ttk.Frame(t, padding=12); f.pack(fill='both', expand=True)
        self.photo = None
        if res.get('sheet') and os.path.isfile(res['sheet']):
            try:
                import base64, io
                from PIL import Image
                with Image.open(res['sheet']) as f0: im = f0.copy()   # closed at once: an open file would stay locked on Windows
                k = app.winfo_fpixels('1i') / 96
                im.thumbnail((round(1100 * k), round(560 * k)))
                buf = io.BytesIO(); im.save(buf, 'PNG')   # Tk reads PNG itself: no PIL.ImageTk needed
                self.photo = tk.PhotoImage(master=t, data=base64.b64encode(buf.getvalue()).decode('ascii'), format='png')
                ttk.Label(f, image=self.photo).pack(anchor='w')
            except Exception:
                log.exception('check window image')
        ok = res.get('ok')
        lines = (['OK: no page errors, seek(t) is pure.' if not bc.is_blend(path) else 'OK: no problems.'] if ok
                 else ['Problems:'] + [f'  • {p}' for p in res.get('problems') or []])
        lines += [f'Note: {n}' for n in res.get('notes') or []]
        if res.get('sheet'): lines.append(f'Sheet: {res["sheet"]}')
        ttk.Label(f, text='\n'.join(lines), justify='left', wraplength=round(1100 * app.winfo_fpixels('1i') / 96),
                  foreground='#3fb950' if ok else '#f85149').pack(anchor='w', pady=(8, 0))
        bt = ttk.Frame(f); bt.pack(anchor='e', pady=(10, 0))
        ttk.Button(bt, text='Open image', command=self.open_image, state='normal' if res.get('sheet') else 'disabled').pack(side='left')
        ttk.Button(bt, text='Close', style='Accent.TButton', command=self.close).pack(side='left', padx=(6, 0))
        t.protocol('WM_DELETE_WINDOW', self.close); t.bind('<Escape>', lambda e: self.close())
        dark_titlebar(t, app.theme == 'dark')
    def open_image(self):
        if self.res.get('sheet'): open_path(self.res['sheet'])
    def close(self): self.top.destroy()
