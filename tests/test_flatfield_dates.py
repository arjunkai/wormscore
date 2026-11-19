"""Two readings of one acquisition date: imaging_date (the per_neuron.csv label, 1.26.13) and acquired_day
(the day of the flat-field imaging session, through session_key).

Nikon and Olympus write the date as the acquiring computer shows it. imaging_date marks a date that reads
both ways (11/3/2009) as not known; acquired_day reads it month first, as it always has, and is not changed:
it decides which slide images correct an image."""
from wormscore import flatfield as FF

AMBIGUOUS = "11/3/2009 12:20:54 PM"          # 3 November or 11 March
DAY_FIRST_PM = "17/7/2009 4:01:38 PM"         # only 17 July: there is no month 17


def test_a_date_that_reads_both_ways_is_marked_in_the_label_and_read_month_first_for_the_session():
    assert FF.imaging_date(AMBIGUOUS) == "11/3/2009 (day or month first: not known)"
    assert FF.acquired_day(AMBIGUOUS) == "2009-11-03"
    assert FF.session_key({"system": "A", "settings": "", "acquired": AMBIGUOUS})[2] == "2009-11-03"


def test_the_same_rule_reads_slide_images_and_images_so_same_day_matching_does_not_depend_on_it():
    """Slide images and images of one computer write the date the same way and go through the same
    session_key, so an image and the slide images of its day get the same day, whichever reading is right.
    Two different days never get the same day: a month-first reading of a day-first date has a day of the
    month of at most 12, and a date read the right way only because its day is above 12 has a day above 12."""
    corr = FF.Correction.__new__(FF.Correction)
    corr.sessions = [{"index": i, "system": "A", "settings": "s", "day": FF.acquired_day(t), "height": 2, "width": 2}
                     for i, t in enumerate(["11/3/2009 9:00:00 AM", "3/11/2009 9:00:00 AM"])]
    assert [s["day"] for s in corr.sessions] == ["2009-11-03", "2009-03-11"]        # two days stay two days
    img = lambda text: {"system": "A", "settings": "s", "acquired": text, "height": 2, "width": 2}
    assert corr.resolve(img("11/3/2009 4:00:00 PM"))["index"] == 0
    assert corr.resolve(img("3/11/2009 4:00:00 PM"))["index"] == 1
    assert corr.resolve(img("11/3/2009 4:00:00 PM"))["how"] == "same session"
    # every pair of day-first dates of one year: two different dates never give one day
    seen = {}
    for month in range(1, 13):
        for day in range(1, 29):
            got = FF.acquired_day(f"{day}/{month}/2009 10:00:00")       # 24-hour day-first: read today
            assert got, (day, month)
            assert seen.setdefault(got, (day, month)) == (day, month), (got, seen[got], (day, month))


def test_a_day_first_date_with_am_pm_gets_no_day_today():
    """Reproduces the gap: the day-first form is known with a 24-hour time only."""
    assert FF.imaging_date(DAY_FIRST_PM) == "2009-07-17"
    assert FF.acquired_day("17/7/2009 16:01:38") == "2009-07-17"
    assert FF.acquired_day(DAY_FIRST_PM) == ""
