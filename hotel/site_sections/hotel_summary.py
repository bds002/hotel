from hotel import *


@all_renderable(c.PEOPLE)
class Root:
    # TODO: handle people who didn't request setup / teardown but who were assigned to a setup / teardown room
    def setup_teardown(self, session):
        attendees = []
        for hr in (session.query(HotelRequests)
                          .filter_by(approved=True)
                          .options(joinedload(HotelRequests.attendee).subqueryload(Attendee.shifts).joinedload(Shift.job))):
            if hr.setup_teardown and hr.attendee.takes_shifts and hr.attendee.badge_status in [c.NEW_STATUS, c.COMPLETED_STATUS]:
                reasons = []
                if hr.attendee.setup_hotel_approved and not any([shift.job.is_setup for shift in hr.attendee.shifts]):
                    reasons.append('has no setup shifts')
                if hr.attendee.teardown_hotel_approved and not any([shift.job.is_teardown for shift in hr.attendee.shifts]):
                    reasons.append('has no teardown shifts')
                if reasons:
                    attendees.append([hr.attendee, reasons])
        attendees = sorted(attendees, key=lambda tup: tup[0].full_name)

        return {
            'attendees': [
                ('Department Heads', [tup for tup in attendees if tup[0].is_dept_head]),
                ('Regular Staffers', [tup for tup in attendees if not tup[0].is_dept_head])
            ]
        }
