def fizzbuzz(n: int) -> list:
    result = []
    for i in range(1, n + 1):
        if i % 5 == 0:
            result.append("Buzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 15 == 0:
            result.append("FizzBuzz")
        else:
            result.append(i)
    return result
