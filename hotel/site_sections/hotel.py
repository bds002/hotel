from hotel import *


def cannot_modify_rooms():
    return c.ROOMS_LOCKED_IN and c.STAFF_ROOMS not in AdminAccount.access_set()


@all_renderable(c.STAFF_ROOMS)
class Root:
    def index(self, session, department=None):
        attendee = session.admin_attendee()
        department = int(department or c.JOB_LOCATION_OPTS[0][0])
        return {
            'department': department,
            'dept_name': c.JOB_LOCATIONS[department],
            'checklist': session.checklist_status('hotel_eligible', department),
            'attendees': session.query(Attendee)
                                .filter_by(hotel_eligible=True)
                                .filter(Attendee.assigned_depts.contains(str(department)))
                                .order_by(Attendee.full_name).all()
        }

    def requests(self, session, department=None):
        dept_filter = []
        requests = session.query(HotelRequests).join(HotelRequests.attendee).options(joinedload(HotelRequests.attendee)).order_by(Attendee.full_name).all()
        if department:
            dept_filter = [Attendee.assigned_depts.contains(department)]
            requests = [r for r in requests if r.attendee.assigned_to(department)]

        return {
            'requests': requests,
            'department': department,
            'declined_count': len([r for r in requests if r.nights == '']),
            'dept_name': 'All' if not department else c.JOB_LOCATIONS[int(department)],
            'checklist': session.checklist_status('approve_setup_teardown', department),
            'staffer_count': session.query(Attendee).filter(Attendee.badge_type == c.STAFF_BADGE, *dept_filter).count()
        }

    def hours(self, session):
        staffers = session.query(Attendee).filter_by(badge_type=c.STAFF_BADGE).order_by(Attendee.full_name).all()
        staffers = [s for s in staffers if s.hotel_shifts_required and s.weighted_hours < 30]
        return {'staffers': staffers}

    def no_shows(self, session):
        staffers = session.query(Attendee).filter_by(badge_type=c.STAFF_BADGE).order_by(Attendee.full_name).all()
        staffers = [s for s in staffers if s.hotel_nights and not s.checked_in]
        return {'staffers': staffers}

    @ajax
    def approve(self, session, id, approved):
        # TODO: turn this on in a week, and in the long run decide whether this should be a separate deadline
        # assert not cannot_modify_rooms(), 'The deadline for modifying rooms has passed'
        hr = session.hotel_requests(id)
        if approved == 'approved':
            hr.approved = True
        else:
            hr.decline()
        session.commit()
        return {'nights': hr.nights_display}

    @csv_file
    def ordered(self, out, session):
        reqs = [hr for hr in session.query(HotelRequests).options(joinedload(HotelRequests.attendee)).all() if hr.nights]
        assigned = {ra.attendee for ra in session.query(RoomAssignment).options(joinedload(RoomAssignment.attendee), joinedload(RoomAssignment.room)).all()}
        unassigned = {hr.attendee for hr in reqs if hr.attendee not in assigned}

        names = {}
        for attendee in unassigned:
            names.setdefault(attendee.last_name.lower(), set()).add(attendee)

        lookup = defaultdict(set)
        for xs in names.values():
            for attendee in xs:
                lookup[attendee] = xs

        for req in reqs:
            if req.attendee in unassigned:
                for word in req.wanted_roommates.lower().replace(',', '').split():
                    try:
                        combined = lookup[list(names[word])[0]] | lookup[req.attendee]
                        for attendee in combined:
                            lookup[attendee] = combined
                    except:
                        pass

        writerow = lambda a, hr: out.writerow([
            a.full_name, a.email, a.cellphone,
            a.hotel_requests.nights_display, ' / '.join(a.assigned_depts_labels),
            hr.wanted_roommates, hr.unwanted_roommates, hr.special_needs
        ])
        grouped = {frozenset(group) for group in lookup.values()}
        out.writerow(['Name', 'Email', 'Phone', 'Nights', 'Departments', 'Roomate Requests', 'Roomate Anti-Requests', 'Special Needs'])
        # TODO: for better efficiency, a multi-level joinedload would be preferable here
        for room in session.query(Room).options(joinedload(Room.room_assignments)).order_by(Room.department).all():
            for i in range(3):
                out.writerow([])
            out.writerow([room.department_label + ' room created by department heads for ' + room.nights_display + (' ({})'.format(room.notes) if room.notes else '')])
            for ra in room.room_assignments:
                writerow(ra.attendee, ra.attendee.hotel_requests)
        for group in sorted(grouped, key=len, reverse=True):
            for i in range(3):
                out.writerow([])
            for a in group:
                writerow(a, a.hotel_requests)

    def assignments(self, session):
        if cannot_modify_rooms():
            cherrypy.response.headers['Content-Type'] = 'text/plain'
            return json.dumps({
                'message': 'Hotel rooms are currently locked in, email {} if you need a last-minute adjustment'.format(c.STAFF_EMAIL),
                'rooms': [room.to_dict() for room in session.query(Room).all()]
            }, indent=4, cls=serializer)
        else:
            attendee = session.admin_attendee()
            three_days_before = (c.EPOCH - timedelta(days=3)).strftime('%A')
            two_days_before = (c.EPOCH - timedelta(days=2)).strftime('%A')
            day_before = (c.EPOCH - timedelta(days=1)).strftime('%A')
            last_day = c.ESCHATON.strftime('%A')
            return {
                'dump': _hotel_dump(session),
                'nights': [{
                    'core': False,
                    'name': three_days_before.lower(),
                    'val': getattr(c, three_days_before.upper()),
                    'desc': three_days_before + ' night (for setup volunteers)'
                }, {
                    'core': False,
                    'name': two_days_before.lower(),
                    'val': getattr(c, two_days_before.upper()),
                    'desc': two_days_before + ' night (for early setup volunteers)'
                }, {
                    'core': False,
                    'name': day_before.lower(),
                    'val': getattr(c, day_before.upper()),
                    'desc': day_before + ' night (for setup volunteers)'
                }] + [{
                    'core': True,
                    'name': c.NIGHTS[night].lower(),
                    'val': night,
                    'desc': c.NIGHTS[night]
                } for night in c.CORE_NIGHTS] + [{
                    'core': False,
                    'name': last_day.lower(),
                    'val': getattr(c, last_day.upper()),
                    'desc': last_day + ' night (for teardown volunteers)'
                }]
            }

    @ajax
    def create_room(self, session, **params):
        params['nights'] = list(filter(bool, [params.pop(night, None) for night in c.NIGHT_NAMES]))
        session.add(session.room(params))
        session.commit()
        return _hotel_dump(session)

    @ajax
    def edit_room(self, session, **params):
        params['nights'] = list(filter(bool, [params.pop(night, None) for night in c.NIGHT_NAMES]))
        session.room(params)
        session.commit()
        return _hotel_dump(session)

    @ajax
    def delete_room(self, session, id):
        room = session.room(id)
        session.delete(room)
        session.commit()
        return _hotel_dump(session)

    @ajax
    def assign_to_room(self, session, attendee_id, room_id):
        room = session.room(room_id)
        for other_room in session.query(RoomAssignment).filter_by(attendee_id=attendee_id).all():
            if set(other_room.nights_ints).intersection(room.nights_ints):
                break  # don't assign someone to a room which overlaps with an existing room assignment
        else:
            attendee = session.attendee(attendee_id)
            ra = RoomAssignment(attendee=attendee, room=room)
            session.add(ra)
            hr = attendee.hotel_requests
            if room.setup_teardown:
                hr.approved = True
            elif not hr.approved:
                hr.decline()
            session.commit()
        return _hotel_dump(session)

    @ajax
    def unassign_from_room(self, session, attendee_id):
        for ra in session.query(RoomAssignment).filter_by(attendee_id=attendee_id).all():
            session.delete(ra)
        session.commit()
        return _hotel_dump(session)


def _attendee_dict(attendee):
    return {
        'id': attendee.id,
        'name': attendee.full_name,
        'nights': getattr(attendee.hotel_requests, 'nights_display', ''),
        'special_needs': getattr(attendee.hotel_requests, 'special_needs', ''),
        'wanted_roommates': getattr(attendee.hotel_requests, 'wanted_roommates', ''),
        'unwanted_roommates': getattr(attendee.hotel_requests, 'unwanted_roommates', ''),
        'approved': int(getattr(attendee.hotel_requests, 'approved', False)),
        'departments': ' / '.join(attendee.assigned_depts_labels),
        'nights_lookup': {night: getattr(attendee.hotel_requests, night, False) for night in c.NIGHT_NAMES},
    }


def _room_dict(session, room):
    return dict({
        'id': room.id,
        'notes': room.notes,
        'nights': room.nights_display,
        'attendees': [_attendee_dict(ra.attendee) for ra in sorted(room.room_assignments, key=lambda ra: ra.attendee.full_name)]
    }, **{
        night: getattr(room, night) for night in c.NIGHT_NAMES
    })


def _get_declined(session):
    return [_attendee_dict(a) for a in session.query(Attendee)
                                              .order_by(Attendee.full_name)
                                              .join(Attendee.hotel_requests)
                                              .filter(Attendee.hotel_requests != None, HotelRequests.nights == '').all()]


def _get_unconfirmed(session, assigned_ids):
    return [_attendee_dict(a) for a in session.query(Attendee)
                                              .order_by(Attendee.full_name)
                                              .filter(Attendee.badge_type == c.STAFF_BADGE,
                                                      Attendee.hotel_requests == None).all()
                              if a not in assigned_ids]


def _get_unassigned(session, assigned_ids):
    return [_attendee_dict(a) for a in session.query(Attendee)
                                              .order_by(Attendee.full_name)
                                              .join(Attendee.hotel_requests)
                                              .filter(Attendee.hotel_requests != None,
                                                      HotelRequests.nights != '').all()
                              if a.id not in assigned_ids]


def _hotel_dump(session):
    rooms = [_room_dict(session, room) for room in session.query(Room).order_by(Room.created).all()]
    assigned = sum([r['attendees'] for r in rooms], [])
    assigned_ids = [a['id'] for a in assigned]
    return {
        'rooms': rooms,
        'assigned': assigned,
        'declined': _get_declined(session),
        'unconfirmed': _get_unconfirmed(session, assigned_ids),
        'unassigned': _get_unassigned(session, assigned_ids)
    }
