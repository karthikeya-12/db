from time import perf_counter
from uuid import uuid7

from termcolor import colored

start = perf_counter()
for i in range(1, 100):
    print(colored(str(uuid7()), "yellow", attrs=["bold"]), end="\n\n")
    print("__ " * 50)
end = perf_counter()
print(f"\n\n{round(end - start, 6)}")
