from fizzbuzz import fizzbuzz


def test_returns_list_of_length_n():
    assert len(fizzbuzz(15)) == 15


def test_multiples_of_three_are_fizz():
    assert fizzbuzz(3)[-1] == "Fizz"


def test_multiples_of_five_are_buzz():
    assert fizzbuzz(5)[-1] == "Buzz"
