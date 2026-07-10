ADULT_AGE = 18


def is_adult(age):
    return age > ADULT_AGE


def is_minor(age):
    return not is_adult(age)
