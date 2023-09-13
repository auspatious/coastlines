# Memory testing using memray

The content of this folder was used for ad-hoc testing of memory usage.

To repeat it, you can install memray `pip install memray` and then run like
this:

```bash
memray run -o scaled.bin tests/memory_testing/mem-test.py
```

I edited the mem-test.py file and switched to unscaled, and compared them
both. You can create a summary of the results with:

```bash
memray flamegraph scaled.bin
```

It wasn't very informative!
