"""Yo'lovchi / haydovchi aniqlash: kamida 30 + 30 misol."""

from __future__ import annotations

import time

import pytest

from bot.relay.detect import classify, extract_phones, load_overrides, normalize

PASSENGERS = [
    "taxi kerak",
    "taksi kerak",
    "Taksi kerak 2 kishi",
    "такси керак",
    "такси нужно",
    "Toshkentga ketaman",
    "Beshariqqa odam bor",
    "1 kishi Toshkentga",
    "pochta bor",
    "mashina kerak",
    "кетаман",
    "ketamiz 2 kishi",
    "Toshkentga 3 kishi",
    "2ta odam bor",
    "Beshariqdan Toshkentga ketamiz",
    "нужно такси",
    "нужна машина",
    "есть посылка",
    "Подвезите до Ташкента",
    "ertalab ketaman",
    "taksi kerek",
    "mashina bormi",
    "Qaqirdan 1 kishi",
    "Ketmoqchiman Beshariqqa",
    "4 kishi bor",
    "yuk bor",
    "gruz bor",
    "posilka bor",
    "boraman Toshkentga",
    "Такси надо срочно",
    "aka manga taksi kerak",
    "hozir ketish kerak",
    "1ta kishi Beshariqqa",
    "Посылка есть в Ташкент",
    "Кетамиз",
    "одам бор",
    "taksi   kerak!!!",
    "taksi kerakmi",
    "joy bormi",
    "Хочу такси",
    "Еду в Ташкент",
    "Taksi керак",
    "taksi menga kerak",
    "Toshkentga kerak",
    "pochta bor Toshkentga",
]

DRIVERS = [
    "joy bor",
    "mashina bor",
    "olib ketaman",
    "Cobalt bo'sh",
    "свободно",
    "beraman",
    "4 joy bor Toshkentga",
    "Gentra bo\u2018sh joy",
    "Beshariqdan 2 kishi kerak",
    "места есть",
    "есть места до Ташкента",
    "3 ta joy qoldi",
    "lacetti bo'sh",
    "kim bor beshariqqa",
    "odam olaman",
    "pochta olaman",
    "https://t.me/ads_bot taksi kerak",
    "http://spam.example taksi kerak",
    "taksi kerak " + ("x" * 500),
    "assalomu alaykum yaxshimisiz",
    "rahmat aka",
    "Cobalt 4 joy",
    "mashinam bor hozir",
    "svobodno 2 mesta",
    "poputchik olaman",
    "бўш машина",
    "Жой бор",
    "одам керак",
    "бераман",
    "Damas bosh 3 kishi olaman",
    "joylar bor arzon",
    "Toshkentga ketaman, 3 joy bor",
    "olib ketamiz hozir",
    "toshkent",
    "salom",
    "taksi",
    "👍",
    "/start",
    "www.reklama.uz",
    "свободна",
    "пусто",
    "4ta joy bor",
    "Beshariq Toshkent",
    "hozir chiqaman",
    "2 kishi kerak",
]


@pytest.fixture(autouse=True)
def _clean_overrides() -> None:
    load_overrides([])
    yield
    load_overrides([])


def test_sample_counts() -> None:
    assert len(PASSENGERS) >= 30
    assert len(DRIVERS) >= 30


@pytest.mark.parametrize("text", PASSENGERS)
def test_passenger_is_relayed(text: str) -> None:
    decision = classify(text)
    assert decision.accept, (text, decision)


@pytest.mark.parametrize("text", DRIVERS)
def test_driver_or_spam_is_not_relayed(text: str) -> None:
    decision = classify(text)
    assert not decision.accept, (text, decision)


def test_normalize_script_and_apostrophe() -> None:
    assert normalize("ТАКСИ КЕРАК") == "taksi kerak"
    assert normalize("такси нужно") == "taksi nujno"
    assert normalize("бўш") == "bosh"
    assert normalize("Cobalt bo'sh") == "cobalt bosh"
    assert normalize("Cobalt bo\u2018sh") == "cobalt bosh"
    assert normalize("bo\u2018sh") == "bosh"
    assert normalize("bo\u02bbsh") == "bosh"
    assert normalize("свободно") == "svobodno"


def test_phone_extracted_without_false_hit() -> None:
    assert extract_phones("1 kishi Toshkentga") == []
    decision = classify("pochta bor Toshkentga tel 90 111 22 33")
    assert decision.accept
    assert decision.phones == ("+998901112233",)
    assert extract_phones("+998 90 111 22 33") == ["+998901112233"]
    assert extract_phones("901112233") == ["+998901112233"]


def test_runtime_word_can_be_added_and_removed() -> None:
    load_overrides([("passenger", "zigzag kerak", 5)])
    assert classify("zigzag kerak").accept
    load_overrides([("off", "ketaman", 0)])
    assert not classify("ketaman").accept
    assert classify("taksi kerak").accept


def test_classify_is_fast() -> None:
    samples = PASSENGERS + DRIVERS
    started = time.perf_counter()
    for _ in range(100):
        for text in samples:
            classify(text)
    per_ms = (time.perf_counter() - started) / (100 * len(samples)) * 1000
    assert per_ms < 0.5, per_ms
