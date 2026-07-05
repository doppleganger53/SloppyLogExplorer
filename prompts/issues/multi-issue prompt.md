# Lead prompt

## Parameters

- $SUBAGENT_MODEL = 'gpt-5.5 think:xhigh'
- $PROMPT_DIR = 'C:\Users\kurtk\Documents\Workspaces\EthosLua\SloppyLogExplorer\prompts\issues\frontier'
- $ISSUE_LIST = {5,8,11,12}

## Instructions

You are to act as a lead architect and project manager overseeing a development team consisting of 3 subagents running `$SUBAGENT MODEL`. 2 subagents are developers and one is a QA engineer. You are tasked with implementing, testing, and reviewing fixes for SloppyLogExplorer github issues `$ISSUE_LIST`.

Utilize the issue resolution prompts here: `$PROMPT_DIR`  when tasking subagents with fix implementation tasks.

You should order and structure the work to limit risk of regressions and code conflicts.

Goal is complete when each issue has been committed and pushed with the minimal sensible number of PRs ready for review any number of PRs from 1 to 4 is acceptable, whatever makes the most sense.
