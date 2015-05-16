"""Teams on Gratipay are plural participants with members.
"""
from collections import OrderedDict
from decimal import Decimal

from aspen.utils import typecheck


class MemberLimitReached(Exception): pass

class StubParticipantAdded(Exception): pass

class MixinTeam(object):
    """This class provides methods for working with a Participant as a Team.

    :param Participant participant: the underlying :py:class:`~gratipay.participant.Participant` object for this team

    """

    # XXX These were all written with the ORM and need to be converted.

    def __init__(self, participant):
        self.participant = participant

    def show_as_team(self, user):
        """Return a boolean, whether to show this participant as a team.
        """
        if not self.IS_PLURAL:
            return False
        if user.ADMIN:
            return True
        if not self.get_current_takes():
            if self == user.participant:
                return True
            return False
        return True

    def member_of(self, team):
        """Given a Participant object, return a boolean.
        """
        assert team.IS_PLURAL
        for take in team.get_current_takes():
            if take['member'] == self.username:
                return True
        return False
