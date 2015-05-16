"""Teams on Gratipay are plural participants with members.
"""
from postgres.orm import Model


class Team(Model):
    """Represent a Gratipay team.
    """

    typname = 'teams'


    # Constructors
    # ============

    @classmethod
    def from_id(cls, id):
        """Return an existing team based on id.
        """
        return cls._from_thing("id", id)

    @classmethod
    def from_slug(cls, slug):
        """Return an existing team based on slug.
        """
        return cls._from_thing("slug_lower", slug.lower())

    @classmethod
    def _from_thing(cls, thing, value):
        assert thing in ("id", "slug_lower")
        return cls.db.one("""

            SELECT teams.*::teams
              FROM teams
             WHERE {}=%s

        """.format(thing), (value,))

    @classmethod
    def create_new(cls, owner, fields):
        return cls.db.one("""

            INSERT INTO teams
                        (slug, slug_lower, name, homepage, product_or_service,
                         getting_involved, getting_paid, owner)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
              RETURNING teams.*::teams

        """, (fields['slug'], fields['slug'].lower(), fields['name'], fields['homepage'],
              fields['product_or_service'], fields['getting_involved'], fields['getting_paid'],
              owner.username))


    def update_receiving(self, cursor=None):
        # Stubbed out for now. Migrate this over from Participant.
        pass

    def get_take_last_week_for(self, member):
        """Get the user's nominal take last week. Used in throttling.
        """
        assert self.IS_PLURAL
        membername = member.username if hasattr(member, 'username') \
                                                        else member['username']
        return self.db.one("""

            SELECT amount
              FROM takes
             WHERE team=%s AND member=%s
               AND mtime < (
                       SELECT ts_start
                         FROM paydays
                        WHERE ts_end > ts_start
                     ORDER BY ts_start DESC LIMIT 1
                   )
          ORDER BY mtime DESC LIMIT 1

        """, (self.username, membername), default=Decimal('0.00'))

    def get_take_for(self, member):
        """Return a Decimal representation of the take for this member, or 0.
        """
        assert self.IS_PLURAL
        return self.db.one( "SELECT amount FROM current_takes "
                            "WHERE member=%s AND team=%s"
                          , (member.username, self.username)
                          , default=Decimal('0.00')
                           )

    def compute_max_this_week(self, last_week):
        """2x last week's take, but at least a dollar.
        """
        return max(last_week * Decimal('2'), Decimal('1.00'))

    def set_take_for(self, member, take, recorder, cursor=None):
        """Sets member's take from the team pool.
        """
        assert self.IS_PLURAL

        # lazy import to avoid circular import
        from gratipay.security.user import User
        from gratipay.models.participant import Participant

        typecheck( member, Participant
                 , take, Decimal
                 , recorder, (Participant, User)
                  )

        last_week = self.get_take_last_week_for(member)
        max_this_week = self.compute_max_this_week(last_week)
        if take > max_this_week:
            take = max_this_week

        self.__set_take_for(member, take, recorder, cursor)
        return take

    def __set_take_for(self, member, amount, recorder, cursor=None):
        assert self.IS_PLURAL
        # XXX Factored out for testing purposes only! :O Use .set_take_for.
        with self.db.get_cursor(cursor) as cursor:
            # Lock to avoid race conditions
            cursor.run("LOCK TABLE takes IN EXCLUSIVE MODE")
            # Compute the current takes
            old_takes = self.compute_actual_takes(cursor)
            # Insert the new take
            cursor.run("""

                INSERT INTO takes (ctime, member, team, amount, recorder)
                 VALUES ( COALESCE (( SELECT ctime
                                        FROM takes
                                       WHERE member=%(member)s
                                         AND team=%(team)s
                                       LIMIT 1
                                     ), CURRENT_TIMESTAMP)
                        , %(member)s
                        , %(team)s
                        , %(amount)s
                        , %(recorder)s
                         )

            """, dict(member=member.username, team=self.username, amount=amount,
                      recorder=recorder.username))
            # Compute the new takes
            new_takes = self.compute_actual_takes(cursor)
            # Update receiving amounts in the participants table
            self.update_taking(old_takes, new_takes, cursor, member)
            # Update is_funded on member's tips
            member.update_giving(cursor)

    def update_taking(self, old_takes, new_takes, cursor=None, member=None):
        """Update `taking` amounts based on the difference between `old_takes`
        and `new_takes`.
        """
        for username in set(old_takes.keys()).union(new_takes.keys()):
            if username == self.username:
                continue
            old = old_takes.get(username, {}).get('actual_amount', Decimal(0))
            new = new_takes.get(username, {}).get('actual_amount', Decimal(0))
            diff = new - old
            if diff != 0:
                r = (cursor or self.db).one("""
                    UPDATE participants
                       SET taking = (taking + %(diff)s)
                         , receiving = (receiving + %(diff)s)
                     WHERE username=%(username)s
                 RETURNING taking, receiving
                """, dict(username=username, diff=diff))
                if member and username == member.username:
                    member.set_attributes(**r._asdict())

    def get_current_takes(self, cursor=None):
        """Return a list of member takes for a team.
        """
        assert self.IS_PLURAL
        TAKES = """
            SELECT member, amount, ctime, mtime
              FROM current_takes
             WHERE team=%(team)s
          ORDER BY ctime DESC
        """
        records = (cursor or self.db).all(TAKES, dict(team=self.username))
        return [r._asdict() for r in records]

    def get_team_take(self, cursor=None):
        """Return a single take for a team, the team itself's take.
        """
        assert self.IS_PLURAL
        TAKE = "SELECT sum(amount) FROM current_takes WHERE team=%s"
        total_take = (cursor or self.db).one(TAKE, (self.username,), default=0)
        team_take = max(self.receiving - total_take, 0)
        membership = { "ctime": None
                     , "mtime": None
                     , "member": self.username
                     , "amount": team_take
                      }
        return membership

    def compute_actual_takes(self, cursor=None):
        """Get the takes, compute the actual amounts, and return an OrderedDict.
        """
        actual_takes = OrderedDict()
        nominal_takes = self.get_current_takes(cursor=cursor)
        nominal_takes.append(self.get_team_take(cursor=cursor))
        budget = balance = self.balance + self.receiving - self.giving
        for take in nominal_takes:
            nominal_amount = take['nominal_amount'] = take.pop('amount')
            actual_amount = take['actual_amount'] = min(nominal_amount, balance)
            if take['member'] != self.username:
                balance -= actual_amount
            take['balance'] = balance
            take['percentage'] = (actual_amount / budget) if budget > 0 else 0
            actual_takes[take['member']] = take
        return actual_takes
