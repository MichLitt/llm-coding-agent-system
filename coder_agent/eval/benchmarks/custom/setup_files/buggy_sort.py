# buggy_sort.py — contains intentional bugs for the agent to find and fix

def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i):  # Bug 1: should be n - i - 1
            if arr[j] > arr[j + 1]:  # Bug 2: index out of range due to Bug 1
                arr[j], arr[j + 1] = arr[j], arr[j]  # Bug 3: swap is wrong
    return arr
