import pytest

import chair_determination as rc


@pytest.mark.parametrize("a,b", [
    ("Brøan", "Broan"), ("Brøan", "Broean"), ("Müller", "Muller"), ("Müller", "Mueller"),
    ("Järvinen", "Jarvinen"), ("Järvinen", "Jaervinen"), ("Þórðarson", "Thordarson"),
    ("Gonçalves", "Goncalves"), ("Åkesson", "Akesson"), ("Åkesson", "Aakesson"),
])
def test_fold_variants_overlap(a, b):
    assert rc.fold_variants(a) & rc.fold_variants(b)


def test_different_names_do_not_overlap():
    assert not rc.fold_variants("Brøan") & rc.fold_variants("Broen")


def test_last_name_drops_suffixes_and_parentheses():
    assert rc.last_name("Dana Smith Jr.") == "Smith"
    assert rc.last_name("Dana Smith (Danny)") == "Smith"
    assert rc.last_name("Dana van der Berg") == "Berg"


def test_match_people_by_last_name_with_accents():
    entries = [("Olaf Broan", "o@example.org"), ("Borje Jarvinen", None)]
    persons = {1: "Börje Järvinen", 2: "Olaf Brøan"}
    assert rc.match_people(entries, persons) == {0: 2, 1: 1}


def test_match_requires_the_same_number_of_people():
    assert rc.match_people([("Olaf Broan", None)], {1: "Olaf Brøan", 2: "Pat Nobody"}) is None
    assert rc.match_people([("Olaf Broan", None), ("Pat Nobody", None)], {1: "Olaf Brøan"}) is None


def test_match_fails_on_a_different_person():
    assert rc.match_people([("Olaf Broan", None), ("Kim Other", None)], {1: "Olaf Brøan", 2: "Pat Nobody"}) is None


def test_same_last_name_is_separated_by_first_initial():
    persons = {1: "Ann Wang", 2: "Bo Wang"}
    assert rc.match_people([("Bo Wang", None), ("Ann Wang", None)], persons) == {0: 2, 1: 1}


def test_same_last_name_and_initial_is_ambiguous_so_no_match():
    persons = {1: "Ann Wang", 2: "Amy Wang"}
    assert rc.match_people([("Ann Wang", None), ("Amy Wang", None)], persons) is None


def test_duplicate_page_entry_counts_once():
    entries = [("Olaf Broan", "a@example.org"), ("Olaf Broan", "b@example.org")]
    assert rc.match_people(entries, {1: "Olaf Brøan"}) == {0: 1, 1: 1}


@pytest.mark.parametrize("page,person", [
    ("Dr. Ann One Ph.D.", "Ann One"), ("Ann One, Ph.D.", "Dr. Ann One"), ("Prof. Ann One", "Ann One"),
    ("Ann One Jr.", "Ann One"), ("Ann One PhD", "Ann One"), ("Ann One M.D.", "Ann One"),
])
def test_titles_and_suffixes_are_ignored(page, person):
    assert rc.match_people([(page, None)], {1: person}) == {0: 1}
    assert rc.same_person(page, person)


def test_family_name_first_matches_the_same_words():
    assert rc.unordered_equal("LARKIN Mei", "Mei Larkin")
    assert rc.match_people([("Mei Larkin", None), ("Pat Nobody", None)], {1: "LARKIN Mei", 2: "Pat Nobody"}) == {0: 1, 1: 2}
    assert rc.match_people([("LARKIN Mei", None)], {1: "Mei Larkin"}) == {0: 1}
    assert rc.same_person("Larkin Mei", "Mei Larkin")
    assert not rc.unordered_equal("Mei Larkin", "Mei Parker")
    assert not rc.unordered_equal("Larkin", "Larkin")  # one word is not an order


def test_alternate_names_of_a_person_are_used():
    persons, alternates = {1: "Ann One", 2: "Bo Two"}, {1: ["Ann Alias"]}
    assert rc.match_people([("Ann Alias", None), ("Bo Two", None)], persons) is None
    assert rc.match_people([("Ann Alias", None), ("Bo Two", None)], persons, alternates) == {0: 1, 1: 2}
    assert rc.judge_names(["Ann Alias", "Bo Two"], persons, alternates) == "exact"
    assert rc.judge_names(["Ann Alias"], persons, alternates) == "subset"


def test_typos_are_not_matched():
    assert rc.match_people([("Quill Marsdan", None)], {1: "Quill Marsden"}) is None
    assert rc.match_people([("Ann Onne", None)], {1: "Ann One"}) is None
    assert not rc.same_person("Ann Onne", "Ann One")


def test_the_refinements_can_be_switched_off_to_measure_them():
    rc.OPTIONS["refined_names"] = False
    try:
        assert rc.match_people([("Ann One Ph.D.", None)], {1: "Ann One"}) is None
        assert rc.match_people([("Mei Larkin", None)], {1: "LARKIN Mei"}) is None
        assert rc.match_people([("Ann Alias", None)], {1: "Ann One"}, {1: ["Ann Alias"]}) is None
    finally:
        rc.OPTIONS["refined_names"] = True
    assert rc.match_people([("Ann One Ph.D.", None)], {1: "Ann One"}) == {0: 1}


def test_person_records_provide_their_alternate_names(tmp_path):
    class Api:
        def scan(self, prefix):
            yield ("/api/v1/person/person/", {"objects": [
                {"id": 1, "name": "Ann One", "ascii": "Ann One", "name_from_draft": "Ann Alias", "plain": "", "ascii_short": ""},
                {"id": 2, "name": "Bo Two", "ascii": "", "name_from_draft": "", "plain": "", "ascii_short": ""}]})

    directory = rc.PersonDirectory(Api())
    assert directory.resolve([1, 2]) == {1: "Ann One", 2: "Bo Two"}
    assert directory.alternates == {1: ["Ann Alias"]}
