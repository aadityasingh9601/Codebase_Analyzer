from testdata import eat_that_frog

s = eat_that_frog

frog_count = s.lower().count("is")
task_count = s.lower().count("the")
focus_count = s.lower().count("and")

print(frog_count)
print(task_count)
print(focus_count)