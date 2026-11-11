"""「Herdr.appで開く」機能のテスト。

方式の決定: docs/permanent/adr/0001-herdr-app-open.md
herdr-app.exe は subprocess をモックして呼ばず、渡した引数を検証する。
"""
import importlib.util
import json
import subprocess
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / 'claude-resume.py'

SESSION_ID = 'abcdef1234567890-session'
FAKE_EXE = '/fake/herdr-app.exe'


@pytest.fixture(scope='module')
def mod():
    # ファイル名にハイフンを含むため import 文では読めない
    spec = importlib.util.spec_from_file_location('claude_resume', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _space(sid, name, machine='wsl', folder='/somewhere'):
    return {'id': sid, 'name': name, 'machine': machine, 'folder': folder,
            'tabs': [{'id': sid.replace('sp', 'tb'), 'name': '1'}]}


class FakeHerdrApp:
    """herdr-app.exe の代役。subprocess.run / Popen への呼び出しを記録する。"""

    def __init__(self, spaces=None, status_results=None, do_error=None):
        self.spaces = spaces or []
        # status の成否を呼び出し順に返す。尽きたら最後の値を使い続ける
        self.status_results = list(status_results or [True])
        self.do_error = do_error
        self.calls = []      # subprocess.run に渡された argv
        self.spawned = []    # subprocess.Popen に渡された argv

    def run(self, argv, *args, **kwargs):
        self.calls.append(list(argv))
        sub = argv[1] if len(argv) > 1 else ''
        if sub == 'status':
            ok = (self.status_results.pop(0) if len(self.status_results) > 1
                  else self.status_results[0])
            if ok:
                return subprocess.CompletedProcess(
                    argv, 0, stdout=json.dumps({'space': None, 'errors': []}), stderr='')
            return subprocess.CompletedProcess(argv, 1, stdout='', stderr='not running')
        if sub == 'ls':
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({'spaces': self.spaces}), stderr='')
        if sub == 'do':
            if self.do_error:
                return subprocess.CompletedProcess(
                    argv, 1, stdout=json.dumps({'error': self.do_error}), stderr='')
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({'space': 'wsl/spNEW', 'tab': 'wsl/tbNEW'}), stderr='')
        raise AssertionError(f'unexpected herdr-app call: {argv}')

    def popen(self, argv, *args, **kwargs):
        self.spawned.append(list(argv))
        return None

    def do_calls(self):
        """do <command> の呼び出しを (command, params) の列で返す。"""
        out = []
        for argv in self.calls:
            if len(argv) > 2 and argv[1] == 'do':
                out.append((argv[2], json.loads(argv[3])))
        return out


@pytest.fixture
def fake(mod, monkeypatch):
    def install(**kwargs):
        f = FakeHerdrApp(**kwargs)
        monkeypatch.setattr(mod, '_herdr_app_path', lambda: FAKE_EXE)
        monkeypatch.setattr(mod.subprocess, 'run', f.run)
        monkeypatch.setattr(mod.subprocess, 'Popen', f.popen)
        monkeypatch.setattr(mod.time, 'sleep', lambda s: None)
        return f
    return install


EXPECTED_ARGV = ['claude', '--resume', SESSION_ID]


# ---- 開く場所 ----

def test_same_name_wsl_space_gets_new_tab(mod, fake):
    f = fake(spaces=[_space('wsl/spA', 'other'), _space('wsl/spB', 'myproj')])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0] == ('tab.new', {
        'space': 'wsl/spB', 'name': SESSION_ID[:8],
        'folder': '/home/u/work/myproj', 'argv': EXPECTED_ARGV})


def test_no_matching_space_creates_space(mod, fake):
    f = fake(spaces=[_space('wsl/spA', 'other')])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0] == ('space.new', {
        'name': 'myproj', 'tab': SESSION_ID[:8], 'folder': '/home/u/work/myproj',
        'machine': 'wsl', 'argv': EXPECTED_ARGV})


def test_windows_space_with_same_name_is_ignored(mod, fake):
    f = fake(spaces=[_space('spWIN', 'myproj', machine=None)])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0][0] == 'space.new'


def test_first_of_duplicate_wsl_spaces_is_used(mod, fake):
    f = fake(spaces=[_space('wsl/spFIRST', 'myproj'), _space('wsl/spSECOND', 'myproj')])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0][1]['space'] == 'wsl/spFIRST'


def test_space_folder_is_not_used_for_matching(mod, fake):
    f = fake(spaces=[_space('wsl/spB', 'myproj', folder='/home/u/work/unrelated')])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0][0] == 'tab.new'
    assert f.do_calls()[0][1]['space'] == 'wsl/spB'


def test_empty_project_uses_default_name_without_folder(mod, fake):
    f = fake(spaces=[])
    mod.launch_in_herdr_app('', SESSION_ID)
    command, params = f.do_calls()[0]
    assert command == 'space.new'
    assert params['name'] == 'claude-resume'
    assert 'folder' not in params


def test_trailing_slash_in_project_is_ignored(mod, fake):
    f = fake(spaces=[_space('wsl/spB', 'myproj')])
    mod.launch_in_herdr_app('/home/u/work/myproj/', SESSION_ID)
    assert f.do_calls()[0][1]['space'] == 'wsl/spB'


# ---- 起動状態 ----

def _launch_spawns(f):
    """ウィンドウを開く／前面化する `herdr-app`（引数なし）の起動回数。"""
    return [a for a in f.spawned if a == [FAKE_EXE]]


def test_running_app_is_not_started_again(mod, fake):
    f = fake(spaces=[], status_results=[True])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    # 前面化の 1 回だけ。起動のための追加呼び出しは無い
    assert len(_launch_spawns(f)) == 1
    # 起動処理より前に開く操作が来ている（status → ls → do の順）
    assert [a[1] for a in f.calls][:1] == ['status']


def test_stopped_app_is_started_then_opened(mod, fake):
    f = fake(spaces=[], status_results=[False, False, True])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    # 起動 1 回 + 前面化 1 回
    assert len(_launch_spawns(f)) == 2
    assert f.do_calls()[0][0] == 'space.new'


def test_app_that_never_responds_raises(mod, fake):
    f = fake(spaces=[], status_results=[False])
    with pytest.raises(RuntimeError, match='Herdr.app の起動を待ちましたが'):
        mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls() == []


# ---- 前面化 ----

def test_foreground_failure_does_not_fail_launch(mod, fake, monkeypatch):
    f = fake(spaces=[])
    calls = {'n': 0}

    def flaky_popen(argv, *args, **kwargs):
        calls['n'] += 1
        raise OSError('cannot spawn')
    monkeypatch.setattr(mod.subprocess, 'Popen', flaky_popen)
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[0][0] == 'space.new'
    assert calls['n'] == 1


def test_created_space_and_tab_are_selected(mod, fake):
    f = fake(spaces=[])
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert f.do_calls()[1:] == [
        ('space.select', {'id': 'wsl/spNEW'}),
        ('tab.select', {'id': 'wsl/tbNEW'}),
    ]


def test_select_failure_does_not_fail_launch(mod, fake, monkeypatch):
    f = fake(spaces=[_space('wsl/spB', 'myproj')])
    original = f.run

    def run_with_select_error(argv, *args, **kwargs):
        if len(argv) > 2 and argv[1] == 'do' and argv[2].endswith('.select'):
            f.calls.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 1, stdout=json.dumps({'error': 'no such tab'}), stderr='')
        return original(argv, *args, **kwargs)
    monkeypatch.setattr(mod.subprocess, 'run', run_with_select_error)
    mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)
    assert [c for c, _ in f.do_calls()] == ['tab.new', 'space.select', 'tab.select']


# ---- エラー ----

def test_missing_exe_raises(mod, fake, monkeypatch):
    fake(spaces=[])
    monkeypatch.setattr(mod, '_herdr_app_path', lambda: None)
    with pytest.raises(RuntimeError, match='Herdr.app が見つかりません'):
        mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)


def test_do_error_message_is_propagated(mod, fake):
    fake(spaces=[], do_error='no such space')
    with pytest.raises(RuntimeError, match='no such space'):
        mod.launch_in_herdr_app('/home/u/work/myproj', SESSION_ID)


# ---- エンドポイント ----

@pytest.fixture
def server(mod):
    httpd = HTTPServer(('127.0.0.1', 0), mod.ClaudeResumeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}'
    httpd.shutdown()
    httpd.server_close()


def _get(base, query):
    with urllib.request.urlopen(f'{base}/api/launch-herdr-app?{query}', timeout=5) as r:
        return json.loads(r.read().decode('utf-8'))


def test_endpoint_success(mod, server, monkeypatch):
    seen = []
    monkeypatch.setattr(mod, 'launch_in_herdr_app', lambda p, s: seen.append((p, s)))
    body = _get(server, f'project=%2Fhome%2Fu%2Fmyproj&session_id={SESSION_ID}')
    assert body['ok'] is True
    assert seen == [('/home/u/myproj', SESSION_ID)]


def test_endpoint_failure_returns_error(mod, server, monkeypatch):
    def boom(p, s):
        raise RuntimeError('no such space')
    monkeypatch.setattr(mod, 'launch_in_herdr_app', boom)
    body = _get(server, f'project=%2Fhome%2Fu%2Fmyproj&session_id={SESSION_ID}')
    assert body == {'ok': False, 'error': 'no such space'}


@pytest.mark.parametrize('query, error', [
    ('project=%2Fhome%2Fu%2Fmyproj', 'session_id required'),
    ('project=%2Fhome%2Fu%2Fmyproj&session_id=bad%3Bid', 'invalid session_id'),
])
def test_endpoint_rejects_bad_session_id(mod, server, monkeypatch, query, error):
    seen = []
    monkeypatch.setattr(mod, 'launch_in_herdr_app', lambda p, s: seen.append((p, s)))
    body = _get(server, query)
    assert body == {'ok': False, 'error': error}
    assert seen == []
