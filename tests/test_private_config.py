import os
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linux'))
from agent_newsbot.private_config import load_private_config


def test_private_config_is_data_and_environment_overrides(tmp_path):
    path=tmp_path/'config.env'; path.write_text('AGENT_NEWSBOT_WEATHER_CITY="New York"\nAGENT_NEWSBOT_PORT=19001\nAGENT_NEWSBOT_WORLDMONITOR_KEY="literal-$value"\n'); path.chmod(0o600)
    env={'AGENT_NEWSBOT_CONFIG':str(path),'AGENT_NEWSBOT_PORT':'19002'}
    load_private_config(env)
    assert env['AGENT_NEWSBOT_WEATHER_CITY']=='New York' and env['AGENT_NEWSBOT_PORT']=='19002'
    assert env['AGENT_NEWSBOT_WORLDMONITOR_KEY']=='literal-$value'
    path.chmod(0o644)
    with pytest.raises(ValueError,match='private'): load_private_config({'AGENT_NEWSBOT_CONFIG':str(path)})


def test_invalid_config_does_not_partially_apply(tmp_path):
    path=tmp_path/'config.env'; path.write_text('AGENT_NEWSBOT_PORT=19001\nPATH=/bad\n'); path.chmod(0o600)
    env={'AGENT_NEWSBOT_CONFIG':str(path)}
    with pytest.raises(ValueError,match='unsupported'): load_private_config(env)
    assert 'AGENT_NEWSBOT_PORT' not in env


def test_private_database_rejects_symlink_and_tightens_file(tmp_path):
    from agent_newsbot.private_files import private_database
    target=tmp_path/'target'; target.write_bytes(b'unchanged'); target.chmod(0o644)
    link=tmp_path/'alias'; link.symlink_to(target)
    with pytest.raises(OSError): private_database(link)
    assert target.read_bytes()==b'unchanged' and target.stat().st_mode&0o777==0o644
    private_database(target)
    assert target.read_bytes()==b'unchanged' and target.stat().st_mode&0o777==0o600
