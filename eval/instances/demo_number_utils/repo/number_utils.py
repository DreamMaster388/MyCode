def is_prime(n: int) -> bool:
    for i in range(2, n):
        if n % i == 0:
            return False
    return True


def count_primes(a: int, b: int) -> int:
    return sum(1 for x in range(a, b + 1) if is_prime(x))
