# -*- coding: utf-8 -*-

from .main import update_qc_lang, update_rating_system, save_state
from .main import load_state, enable_channel, disable_channel
from .main import remove_players, expire_auto_ready

from .queue_channel import QueueChannel
from .queues.pickup_queue import PickupQueue
from .queues.common import QueueResponses as Qr
from .match.match import Match
from .expire import expire
from .stats import stats
from .stats.noadds import noadds
from .stats.autoreply import autoreply
from .queues.daily_clear import daily_clear
from .exceptions import Exceptions as Exc
from .context import Context, SlashContext, SystemContext
from . import commands

from . import events


bot_was_ready = False
bot_ready = False
queue_channels = dict()  # {channel.id: QueueChannel()}
active_queues = []
active_matches = []
# {message.id: callback}. Only the party-up ✅ uses it now: check-in is
# buttons only (Nov 26), and every match exit (finish, abort, cancel, a
# crash) removes the party entry, so the old 30-minute expiry sweep went too.
waiting_reactions = dict()
allow_offline = []  # [user_id]
auto_ready = dict()  # {user.id: timestamp}
