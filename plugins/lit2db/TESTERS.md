# Trying lit2db

Thanks for testing this. lit2db turns a research question into a small database built from
published papers, where every value carries the sentence it came from. You describe what you
want in plain words; it searches the literature, reads the papers it can legally get, and gives
you spreadsheets.

This is an early test. Things will break, and when they do, that is exactly what we need to hear.

## What you need

- A Mac, and [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) signed in to
  your Claude account. The run uses your own Claude plan.
- About an hour for a first database of around 20 papers, most of it waiting.

## Set it up (once)

Open Terminal and start Claude Code:

```
claude
```

Then type these two lines into Claude Code:

```
/plugin marketplace add ColdMountain24/lit2db-marketplace
/plugin install lit2db@lit2db-marketplace
```

Quit Claude Code (type `/exit`), and you're set up.

## Build a database

1. Make a folder for it and start Claude Code there:

   ```
   mkdir ~/my-first-database
   cd ~/my-first-database
   claude
   ```

2. Type `/lit2db-start` followed by what you want, in your own words. For example:

   ```
   /lit2db-start which outcomes vitamin D trials measured, and whether supplementation changed them
   ```

3. Answer its questions. The first time, it may ask to install a small tool called uv, which
   gives the plugin its own copy of Python without touching yours. Say yes, then quit and
   restart Claude Code when it tells you to.

4. It will show you how many papers your question finds and how many it can actually read,
   before anything runs. Then it runs, which takes a while. You can leave it.

## What you get

Everything lands in a `lit2db` folder inside your folder. The part you'll want is `lit2db/results/`:

- **papers.csv**: every paper the search found, and whether it could be read
- **candidates.csv**: values it found but was not sure enough to accept, each with the quote and
  the reason
- **accepted.csv**: values that passed every check

Expect `accepted.csv` to be short at first. That's on purpose: the tool would rather hold a value
back for you to check than let a wrong one in.

## When nothing is accepted

That is the normal first result, not a failure. The bar ships high on purpose, so values wait for
you rather than entering the table unchecked. Tell Claude you want to review, and a page opens
with each held value beside the sentence it came from. Ruling on a few dozen is what lets the
bar be set from your own judgement instead of a guess.

## What we'd love to hear

Send AP a message with any of these, even one line:

- Where you got stuck, and what the screen said
- A value in `accepted.csv` that is wrong (with the row)
- Whether `candidates.csv` saved you time or made work
- Anything it asked you that you didn't understand

## Known limits

- It reads only papers that are free to read in Europe PMC. For many topics that's a minority, and
  for the rest it can often use the abstract only.
- It searches Europe PMC, so it covers biomedical and life-science literature, not other fields yet.
- Your papers, database and results stay on your computer. The papers are sent to Claude to be
  read, the same as anything else you do in Claude Code.
