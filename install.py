"""Install EJU beta into new local paths. Standard library only; Python 3.12-3.14."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import venv
import tempfile
import uuid

HERE = Path(__file__).resolve().parent
VERSION = '1.0.0b11'


def runtime_python(root, platform=None):
    return Path(root) / '.venv' / ('Scripts/python.exe' if (platform or os.name) == 'nt' else 'bin/python')


def checked_path(raw):
    path = Path(raw).expanduser().absolute()
    if any(c in str(path) for c in '\r\n\x00'):
        raise ValueError('Paths may not contain line breaks or NUL.')
    # Reject links at every existing component, including Windows junctions.
    for part in (path, *path.parents):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or bool(getattr(info,'st_file_attributes',0)&0x400):
            raise ValueError(f'Linked/reparse path is not supported: {part}')
    return path


def preflight(app, vault, workspace):
    paths = [checked_path(p) for p in (app, vault, workspace)]
    for path in paths:
        if path.exists():
            raise ValueError(f'Destination already exists; choose a new path: {path}')
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path == other or path.is_relative_to(other) or other.is_relative_to(path):
                raise ValueError('App, vault and workspace must be separate, non-nested paths.')
    return paths


def pip_command(python, lock, wheelhouse=None):
    command = [str(python), '-m', 'pip', '--isolated', 'install', '--require-hashes', '--only-binary=:all:', '-r', str(lock)]
    if wheelhouse:
        command += ['--no-index', '--find-links', str(Path(wheelhouse).resolve())]
    else:
        command += ['--index-url', 'https://pypi.org/simple']
    return command



def verify_release_manifest(source):
    manifest=source/'manifest.json'
    if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size>2_000_000:
        raise ValueError('Release manifest missing or invalid; use the complete release archive.')
    payload=json.loads(manifest.read_text(encoding='utf-8'));rows=payload.get('files')
    if payload.get('format')!='eju-release-manifest' or payload.get('version')!=VERSION or not isinstance(rows,dict) or not 1<=len(rows)<=2000:
        raise ValueError('Release manifest format/version is invalid.')
    for name,entry in rows.items():
        if not isinstance(name,str) or not name or '\\' in name or ':' in name or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or any(x in ('','.','..') for x in name.split('/')):
            raise ValueError('Release manifest contains an unsafe path.')
        path=source/Path(*name.split('/'))
        if any(x.is_symlink() or (hasattr(x,'is_junction') and x.is_junction()) for x in (path,*path.parents) if x.is_relative_to(source)):
            raise ValueError('Release files cannot be links.')
        if not isinstance(entry,dict) or not path.is_file() or path.stat().st_size!=entry.get('size') or hashlib.sha256(path.read_bytes()).hexdigest()!=entry.get('sha256'):
            raise ValueError('Release content differs from manifest: '+name)
    actual={x.relative_to(source).as_posix() for x in source.rglob('*') if x.is_file() and x.name!='manifest.json' and '__pycache__' not in x.parts and x.name!='.DS_Store'}
    if actual!=set(rows):raise ValueError('Release contains unexpected or missing files.')
    return True


def state_write(app, state):
    target=app/'installation.json'
    if target.is_symlink() or (hasattr(target,'is_junction') and target.is_junction()):
        raise ValueError('Installation state cannot be a link.')
    descriptor,name=tempfile.mkstemp(prefix='.installation-',dir=app)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as handle:
            json.dump(state,handle,ensure_ascii=False,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
        os.replace(name,target)
    finally:Path(name).unlink(missing_ok=True)


def state_read(app):
    path=checked_path(app/'installation.json')
    if not path.is_file() or path.stat().st_size>65536 or path.stat().st_nlink!=1:
        raise ValueError('Installation state is missing or unsafe.')
    result=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(result,dict):raise ValueError('Invalid installation state.')
    return result


def copy_payload(app):
    # An interrupted copy can be completed only with byte-identical existing files.
    names=('core','eju-study-coach-codex')
    singles=('launch.py','install.py','requirements-lock.txt','INSTALL.md','import_official.py','README.md','SECURITY.md','PRIVACY.md','THIRD_PARTY_NOTICES.md','RELEASE_NOTES.md','LICENSE.txt','WINDOWS_VALIDATION.md')
    rows=[p for name in names for p in (HERE/name).rglob('*') if p.is_file() and not any(x in ('.venv','__pycache__','.DS_Store') for x in p.parts) and p.suffix!='.pyc']
    rows += [HERE/name for name in singles if (HERE/name).is_file()]
    expected={p.relative_to(HERE) for p in rows}
    for name in names:
        folder=checked_path(app/name)
        if folder.exists():
            for existing in folder.rglob('*'):
                checked_path(existing)
                if existing.is_file() and '__pycache__' not in existing.parts and existing.relative_to(app) not in expected:
                    raise ValueError('Unexpected file in interrupted payload; preserve it and choose new paths.')
    for source in rows:
        dest=checked_path(app/source.relative_to(HERE))
        if dest.exists():
            if not dest.is_file() or dest.stat().st_nlink!=1 or source.read_bytes()!=dest.read_bytes():
                raise ValueError('Interrupted payload differs from source; no overwrite: '+str(dest))
        else:
            dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with dest.open('xb') as handle:handle.write(source.read_bytes())


def install(app, vault, workspace, *, allow_network=False, wheelhouse=None, resume=False, upgrade_from=None):
    if not (3, 12) <= sys.version_info[:2] <= (3, 14):
        raise ValueError('Python 3.12, 3.13 or 3.14 is required.')
    if not wheelhouse and not allow_network:
        raise ValueError('Choose --allow-network for locked PyPI dependencies or --wheelhouse PATH for offline setup.')
    if wheelhouse and not Path(wheelhouse).is_dir():raise ValueError('Wheelhouse must be an existing local directory.')
    verify_release_manifest(HERE)
    source_hash=hashlib.sha256((HERE/'manifest.json').read_bytes()).hexdigest()
    lock=HERE/'requirements-lock.txt'
    if not lock.is_file() or '--hash=sha256:' not in lock.read_text(encoding='utf-8'):
        raise ValueError('Release hash-locked dependencies are missing; no installation started.')
    old_vault=None;old_workspace=None
    if upgrade_from:
        old_app=checked_path(upgrade_from);old=state_read(old_app)
        if old.get('stage')!='complete' or old.get('version') not in ('1.0.0b1','1.0.0b2','1.0.0b3','1.0.0b4','1.0.0b5','1.0.0b6','1.0.0b7','1.0.0b8','1.0.0b9','1.0.0b10','1.0.0b11') or old.get('app')!=str(old_app):
            raise ValueError('Upgrade source must be a completed Beta installation.')
        old_vault=checked_path(old['vault']);old_workspace=checked_path(old['workspace'])
        if not (old_vault/'learning.sqlite').is_file():raise ValueError('Upgrade source vault is missing.')
    if resume:
        app=checked_path(app);state=state_read(app)
        if state.get('version')!=VERSION or state.get('app')!=str(app) or state.get('source_manifest_sha256')!=source_hash or not state.get('installation_id'):
            raise ValueError('Resume requires this exact release and its own recorded installation.')
        phase=state.get('failed_stage') if state.get('stage')=='failed' else state.get('stage')
        if phase not in ('copying','creating_venv','installing_dependencies'):
            raise ValueError('Automatic retry is supported before vault creation only. Preserve the recorded vault and workspace; see INSTALL.md for recovery.')
        vault=checked_path(state['vault']);workspace=checked_path(state['workspace'])
        if vault.exists() or workspace.exists():raise ValueError('Retry will not adopt or overwrite an existing vault/workspace.')
        saved_upgrade=state.get('upgrade_from')
        if saved_upgrade != (str(checked_path(upgrade_from)) if upgrade_from else None):
            raise ValueError('Retry must preserve the recorded upgrade source.')
        # Refuse links throughout a partially-created interpreter before executing it.
        if (app/'.venv').exists():
            for file in (app/'.venv').rglob('*'):checked_path(file)
    else:
        app,vault,workspace=preflight(app,vault,workspace)
        state={'version':VERSION,'stage':'created','app':str(app),'vault':str(vault),'workspace':str(workspace),'created_paths':[str(app)],'installation_id':str(uuid.uuid4()),'source_manifest_sha256':source_hash,'upgrade_from':str(old_app) if upgrade_from else None}
    paths=(app,vault,workspace)
    for i,path in enumerate(paths):
        if any(path==other or path.is_relative_to(other) or other.is_relative_to(path) for other in paths[i+1:]):
            raise ValueError('App, vault and workspace must be separate, non-nested paths.')
    for path in paths:
        if path==HERE or path.is_relative_to(HERE) or HERE.is_relative_to(path):
            raise ValueError('Install destinations must be outside the release source directory.')
        if old_vault and any(path==old or path.is_relative_to(old) or old.is_relative_to(path) for old in (old_vault,old_app,old_workspace)):
            raise ValueError('New installation must be separate from the old app, vault and workspace.')
    if not resume:app.mkdir(parents=True,mode=0o700)
    state['network_dependencies_authorized']=bool(allow_network and not wheelhouse)
    def save(stage):state['stage']=stage;state_write(app,state)
    save('copying')
    try:
        copy_payload(app)
        save('creating_venv')
        # Copies make retry link checks portable without following interpreter links.
        venv.EnvBuilder(with_pip=True,symlinks=False).create(app/'.venv')
        python=runtime_python(app)
        save('installing_dependencies')
        subprocess.run(pip_command(python,app/lock.name,wheelhouse),check=True)
        state['attempted_new_paths']=[str(vault)]
        save('initializing_vault')
        if old_vault:
            snapshot=app/'migration-backup'
            subprocess.run([str(python),str(app/'core/run.py'),'--vault',str(old_vault),'backup',str(snapshot)],check=True)
            subprocess.run([str(python),str(app/'core/run.py'),'restore',str(snapshot),str(vault)],check=True)
            state['migration_backup']=str(snapshot);state['source_vault']=str(old_vault)
        else:subprocess.run([str(python),str(app/'core/run.py'),'init',str(vault)],check=True)
        state['created_paths'].append(str(vault));state['attempted_new_paths'].append(str(workspace))
        save('configuring_workspace')
        subprocess.run([str(python),str(app/'core/configure_codex.py'),'--python',str(python),'--workspace',str(workspace),'--vault',str(vault)],check=True)
        state['created_paths'].append(str(workspace))
        (app/'打开工作台.cmd').write_text('@echo off\nsetlocal DisableDelayedExpansion\n"%~dp0.venv\\Scripts\\python.exe" "%~dp0launch.py" workbench\npause\n',encoding='utf-8')
        (app/'打开工作台.command').write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec .venv/bin/python launch.py workbench\n',encoding='utf-8');(app/'打开工作台.command').chmod(0o700)
        save('checking_vault')
        subprocess.run([str(python),str(app/'core/run.py'),'--vault',str(vault),'doctor'],check=True)
        state.pop('failed_stage',None)
        save('complete');return state
    except Exception:
        state['failed_stage']=state['stage'];save('failed')
        print(f'安装未完成，已有文件均保留。进度：{app / "installation.json"}。建库前可从同一发布包加 --resume --app 重试；建库后按 INSTALL.md 恢复。',file=sys.stderr)
        raise


def wizard():
    if not sys.stdin.isatty():raise ValueError('The setup wizard requires a local interactive terminal.')
    base=Path.home()/'EJU-Codex-Beta'
    print('EJU Codex Beta 11 · 本机安装向导\n需要 Python 3.12–3.14。不会下载 OCR 模型。')
    mode=input('安装方式：1 新安装；2 重试中断的安装；3 从已完成的 Beta 安装复制升级 [1]：').strip() or '1'
    if mode not in ('1','2','3'):raise ValueError('请选择 1、2 或 3。')
    def path(prompt,default):return Path(input(prompt+' ['+str(default)+']：').strip() or default).expanduser().absolute()
    app=path('新程序目录' if mode!='2' else '上次安装的程序目录',base/'app-1.0.0b11')
    if mode=='2':
        prior=state_read(checked_path(app));vault=Path(prior['vault']);workspace=Path(prior['workspace']);upgrade=prior.get('upgrade_from')
    else:
        vault=path('新的私人学习库目录',base/'private-vault-b11');workspace=path('新的 Codex 学习项目目录',base/'Codex 学习项目 Beta 11')
        upgrade=path('旧版本程序目录',base/'app-1.0.0b1') if mode=='3' else None
    print('依赖方式：1 从 PyPI 下载锁定依赖（联网）；2 使用本地 wheel 文件夹（离线）。')
    dependency=input('请选择 [2]：').strip() or '2'
    if dependency not in ('1','2'):raise ValueError('请选择 1 或 2。')
    wheels=None
    if dependency=='2':
        raw=input('本地 wheel 文件夹：').strip()
        if not raw:raise ValueError('请提供本地 wheel 文件夹，或重新选择联网安装。')
        wheels=Path(raw).expanduser()
    print(f'程序：{app}\n学习库：{vault}\nCodex 项目：{workspace}')
    if upgrade:print('将旧学习库复制到新库。切换后请只使用新学习项目；两份库不会自动合并。')
    if input('输入 INSTALL 开始安装：').strip()!='INSTALL':
        print('已取消，未开始安装。');return None
    return dict(app=app,vault=vault,workspace=workspace,allow_network=dependency=='1',wheelhouse=wheels,resume=mode=='2',upgrade_from=upgrade)


def main(argv=None):
    base = Path.home() / 'EJU-Codex-Beta'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=base / 'app-1.0.0b11')
    parser.add_argument('--vault', type=Path, default=base / 'private-vault')
    parser.add_argument('--workspace', type=Path, default=base / 'Codex 学习项目')
    parser.add_argument('--allow-network', action='store_true', help='Authorize downloading hash-locked dependencies from PyPI only; no OCR/models.')
    parser.add_argument('--wheelhouse', type=Path, help='Offline local directory containing all locked wheels; pip uses --no-index.')
    parser.add_argument('--wizard',action='store_true',help='Interactive first-use setup')
    parser.add_argument('--resume',action='store_true',help='Retry this exact release before vault creation')
    parser.add_argument('--upgrade-from',type=Path,help='Copy a completed Beta installation into new app/vault/workspace paths')
    args = parser.parse_args(argv)
    try:
        options=wizard() if args.wizard else dict(app=args.app,vault=args.vault,workspace=args.workspace,allow_network=args.allow_network,wheelhouse=args.wheelhouse,resume=args.resume,upgrade_from=args.upgrade_from)
        if options is None:return 0
        result=install(**options)
    except (OSError, ValueError, KeyError, EOFError, subprocess.SubprocessError) as exc:
        print(f'Setup failed: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
