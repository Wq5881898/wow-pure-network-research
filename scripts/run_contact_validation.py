"""Single authorized contact validation; synchronous owner retained through ending."""
import os
import runpy
import sys

os.environ.update(
    AGENT_WOW_CLI='/mnt/c/Users/qrz/AppData/Local/Temp/opencode/local-realm-cli/agent-wow-cli-local',
    AGENT_WOW_LOCAL_REALM_ID='1', AGENT_WOW_LOCAL_REALM_ADDRESS='127.0.0.1:8085',
    AGENT_WOW_NAV='/mnt/c/Users/qrz/AppData/Local/Temp/opencode/tiger-nav-build/corridor_ab_checked',
    AGENT_WOW_MMAPS='/mnt/c/Azerothcore/azerothcore-wotlk/build/bin/Debug/data/mmaps',
    AGENT_WOW_NAV_CORRIDOR_AB='1')
sys.argv = ['efficiency_session.py', '--run-id', 'contact_fixed_20261110_dt',
            '--seconds', '300', '--entry', '3125', '--min-level', '6', '--max-level', '6',
            '--stop-level', '9', '--hunt-goal', '-731.151367', '-5039.376953', '15.9289',
            '--hunt-stop-distance', '30', '--return-relative', '--escape-anchor',
            '-581.734', '-4109.51', '43.67351', '--patrol-plan',
            'results/pure_network/scorp_patrol_plan_20261110_dj.json', '--max-target-wait', '60']
runpy.run_module('efficiency_session', run_name='__main__')
