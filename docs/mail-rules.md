# Mail rules

Mail rules sort your Gmail for you. You write each rule in your own words ("newsletters I never read: archive them"), and CleanSync asks an AI model of your choice which rule fits each new inbox mail. It can then archive the mail, add a label, mark it read, star it, or offload it (out of Gmail, with CleanSync's copy kept). Rules belong to one mailbox. CleanSync still never sends mail.

You choose the model: one running on your own machine (Ollama, LM Studio, llama.cpp, vLLM), your own LiteLLM gateway, or a hosted one such as OpenRouter's free models. No cloud AI service is needed.

## 1. Set up a model

Open **Settings → Models**.

1. Press **Add connection**. Give it a name and the server's address. The address usually ends in `/v1`. Some examples:
   - Ollama in the same Docker network: `http://ollama:11434/v1`
   - A LiteLLM gateway: `http://litellm:4000/v1`, with the gateway's virtual key as the API key
   - OpenRouter: `https://openrouter.ai/api/v1`, with your OpenRouter key. Pick a model whose name ends in `:free` if you don't want to pay.

   The API key and any extra headers are optional. Once saved they are never shown again (see [security](security.md)). Don't put a user name or password in the address; CleanSync refuses it. Use the API key field instead.

   If you later change a connection to another server (another host or port, not just another path), its saved key and headers are removed, so they never go to the new server. Type them again in the same edit if the new server needs them.
2. Under **Models for each task**, pick the connection and the model for each task:
   - **Sorting** decides which rule fits each mail.
   - **Writing rules** turns your sentence into a rule. When it isn't set, it uses the Sorting model.

   **List models** asks the server which models it has. Temperature 0 and a 60-second timeout are good defaults.
3. Press **Save**, then **Test**. Test sends one made-up mail (a weekly digest) and two made-up rules (Receipts and Newsletters) to the model. It shows how long the answer took, which rule it picked, and which way of answering worked. A good model picks Newsletters.

Many free models have a daily limit. If yours does, set **Daily limit resets at** (and its **Time zone**) in the connection. CleanSync uses it when the provider says the limit is used up but doesn't say when it starts again. Most providers reset at 00:00 UTC, which is the default. Gemini resets at midnight Pacific time, so for Gemini choose 00:00 and the time zone `America/Los_Angeles`.

If you paste an address that ends in `/chat/completions`, CleanSync shortens it to the part before it.

CleanSync talks to any server that speaks the OpenAI chat API. It first asks for a strict JSON answer; if the server can't do that, it asks for plain JSON, then for a plain line like `Rule: 2`. It remembers which one worked for each model.

## 2. Write rules

Open **Mail → Rules**. The rules are for the mailbox picked in the account switcher.

**From a sentence.** Type what you want in the box at the top, for example "Archive receipts from shop.example.com", and press **Write the rule**. The model fills in the rule form: a name, which mail it is for, what it does, and any fixed conditions it can see in your sentence. Check it and press **Save rule**. Nothing is saved before that. Without a model, the form opens with your sentence and you fill in the rest.

**From mail you picked.** In the mail list, tick some mails (or open one) and press **Make a rule…**. CleanSync fills in what the mails have in common (the same sender or domain, an unsubscribe link, a calendar invite), and the model writes a short description. Edit it and save.

**The rule form:**
- **Which mail is this for?** The description, in plain words. This is what the model reads.
- **What it does:** Archive, Label (the Gmail label is made if it doesn't exist), Mark read, Star, or Offload. Offload stands alone. **Delete forever** deletes the mail from Gmail and CleanSync's copy. It needs **Allow permanent delete** on for the mailbox (Settings → Accounts), always suggests first (it can't be Automatic), and stands alone. Approving it asks you to type how many mails will be deleted forever. It can't be undone. Correcting a Delete forever rule from a mail's chip only suggests it again; it never deletes anything. A rules file from an older version that says `delete` is read as Offload.
- **Fixed conditions** (optional): From, Not from, Subject contains, Has an unsubscribe link, Is a calendar invite, Has attachments, and Sent to me / CC / a list. All of them must hold.
- **How these work with the description**, when there are fixed conditions:
  - *Only the fixed conditions:* no model is asked.
  - *Fixed conditions decide, else ask the AI:* a match is enough; otherwise the model decides.
  - *Fixed conditions narrow it, the AI confirms:* only matching mail is offered to the model for this rule.
  - *Only the AI:* the description alone decides.
- **Mode:** *Suggest first* (the default) or *Automatic*.
- **Try it** runs the rule as it stands in the form, next to your other rules, on a search, or on the latest 50 inbox mails when the search is empty. It shows which rule each mail would get and why. It changes nothing.

Rules are checked in order (**Move up**, **Move down**, or drag). A mail gets at most one rule. **Starter rules…** adds ready-made rules (Newsletters, Marketing, Receipts, Notifications, Cold outreach, Calendar) in Suggest mode. **Export** and **Import…** save and load a mailbox's rules as a YAML file, and **Copy to another mailbox…** copies them.

## 3. Sorting new mail

Turn on **Sort new inbox mail** at the top of the Rules page. From then on, each new mail that lands in the inbox is sorted a few minutes after the backup brings it in. Mail already in the inbox is left alone (see "Running rules by hand" below).

What happens next depends on the rule's mode:
- **Suggest first:** nothing changes in Gmail. The mail gets a chip such as "Newsletters · suggested" and shows in the **Suggested** view in the Mail panel. Click the chip to see why, then **Approve** or **Correct…**. The Suggested view also has **Approve all**.
- **Automatic:** the rule acts in Gmail right away, and the chip says "applied".

Every change can be undone: click an "applied" chip and press **Undo**. Undo only puts back what CleanSync changed; anything you changed yourself since stays as you left it.

**Correct…** tells CleanSync which rule the mail should have had, or "No rule". An applied change is undone first, then the right rule acts. Corrections are passed to the model as hints for later mail from the same sender.

**Remembered senders.** When you approve or correct mail from the same sender into the same rule three times, CleanSync remembers that sender for that rule and stops asking the model about them. Remembered senders are listed on the rule; **Forget** removes one.

**Your own moves win.** If CleanSync archived a mail and you put it back in the inbox, it is marked "moved back by you" and never sorted again. If you move a mail before approving its suggestion, the suggestion is dropped.

The Mail status line shows how sorting is going: how many mails were sorted today, how many are waiting, and how many suggestions there are.

## 4. Running rules by hand

Sort new inbox mail only looks at new mail. To run rules on mail you already have:

- **Run on a rule card** (Mail → Rules) runs that one rule over your **Inbox**: every mail in Gmail with the Inbox label. Each mail is checked against this rule alone (its remembered senders, its fixed conditions, then the model with this rule as the only choice). Your other rules, and their order, play no part. The button is greyed out while the rule is off, and when the rule needs the model and no Sorting model is set up; the reason shows next to it. For mail outside the Inbox, use a search.
- **Run rules on a search or a pick.** In Gmail's views (Inbox, Starred, Important, Sent, Archived, All mail and your labels), **Run rules** shows above the list when the search box isn't empty, and on the bar when you have picked mail (by hand, or with **Pick all**). It runs on every mail of the search in that view, or on the picked mail. It isn't offered on a view with no search and nothing picked, or in Suggested, Offloaded or Deleted in Gmail. First tick the rules to run (only rules that are on are listed). Each mail gets at most one rule: the first ticked rule that fits, in the order of your rules.

Then:

1. **Deciding.** CleanSync decides each mail using only its own copy. Nothing changes in Gmail yet. You can close the window: the run keeps deciding, and the Rules page says so, with an **Open** button that shows it again.
2. **The list of matched mails.** It says what each rule would do and how many mails didn't fit, then lists the matched mails 50 at a time (**Show more** for the rest), each with its sender, subject, date and the reason. With several rules, each row shows its rule, and **Show** narrows the list to one rule. Every mail is picked at first (**Pick all**); untick the ones to leave alone. The button names the change and the count, for example **Archive 412 mails**, or **Apply 442 changes** when several rules or actions are involved. Pressing it is your approval, whatever each rule's mode is. If any picked mail would be offloaded, you first type the number of those mails.
3. **Making the changes.** The changes are made one by one. You can close the window; it keeps going. **Stop** stops it. A mail that changed after it was decided (or whose rule you changed meanwhile) is left as it is and counted as skipped. The end shows how many were applied, skipped (with the reasons), failed, and left out by you. Mail you left out stays as it is.

Mail that sorting is still deciding, and mail you moved back yourself, are skipped. Closing the window keeps the run: the Rules page shows it with an **Open** button, and nothing is changed until you approve. Only one run at a time per mailbox: starting a new one replaces a run that you haven't confirmed, but while changes are being made you have to wait or press **Stop** first. One run covers at most 20,000 mails. Narrow the search for more.

## 5. Offload and Delete forever

**Offload** (a rule action, or Offload on one mail in the reader) first checks CleanSync's copy of the mail, the same way an offload does. Then it moves the mail to Gmail's trash and marks it Offloaded. Gmail empties its trash on its own after 30 days, and CleanSync can't empty it sooner (see [mail backup](mail-backup.md)). The mail shows in **Offloaded**, beside mail offloaded by a filter or a pick; it isn't part of any offload batch. Undo on the rule's chip takes it out of Gmail's trash again; **Restore to Gmail** works too, and imports it again if Gmail no longer has it. With Allow permanent delete, a rule's Offload and Offload on one mail delete the mail from Gmail directly instead of moving it to the trash. Undo can't bring such a mail back; use **Restore to Gmail** in Offloaded. If the mailbox stops allowing permanent delete, Delete forever rules stop applying: their suggestions wait with "Turn on Allow permanent delete in Settings → Accounts." and the rule card says the same. **Delete forever** in Offloaded removes CleanSync's own copy for good; see [mail backup](mail-backup.md#delete-forever). If you take an offloaded mail out of Gmail's trash yourself, CleanSync sees it on its next check and moves it back to your normal mail.

## 6. What the model sees

For each mail, the model gets your rules (by number, with their descriptions) and:
- the sender, the recipients, and whether you are in To or CC;
- the subject and date;
- the names of the attachments (not their content);
- whether it has an unsubscribe link and whether it is a calendar invite;
- up to 2,000 characters of the mail's text, with quoted replies cut off and every link reduced to its domain.

All of this comes from CleanSync's own copy of the mail; Gmail is never read for sorting. Nothing else about your mail leaves the server. The mail is put inside clear markers, and the model is told it is data, not instructions.

**The model's server sees this text.** With a model on your own machine, it stays with you. With a hosted model, it goes to that company. Use a local model if that matters to you.

## 7. When the model is down

No mail is ever sorted on a guess. When the model can't answer, CleanSync stops calling that connection for a while, and the mail waits. How long depends on what went wrong:

- **The provider says its limit is used up.** CleanSync stops calling the connection until the time the provider gave. If the provider gave none, it waits until the connection's daily reset time (see "Set up a model"). If the provider only asks for a short wait, such as a minute, CleanSync waits just that long.
- **The model can't be reached** (the server is off, the network is down, or it is too slow). CleanSync waits 1 minute, then 5, 15 and 60 minutes, and then 6 hours between tries. The first answer that works starts the count again.
- **The settings are wrong** (the key is refused, there is no model with that name, or the address is wrong). Waiting won't fix this, so CleanSync stops until you change the connection or the task, or press **Resume now**, or run a **Test** that works.

While a connection is paused:

- The whole connection is paused, for every mailbox and every task that uses it. Run rules, Try it and Write the rule use it too: they tell you the model is paused and change nothing.
- Mail doesn't use up its tries, and nothing is lost. The mail is sorted when the pause ends.
- Rules with only fixed conditions, and remembered senders, keep working without a model.
- The pause is saved. It is still there after CleanSync restarts.

You see a pause in three places: the Mail status line ("Sorting is waiting. …", with the reason and the time), the Run rules window, and Settings → Models, next to the connection ("Paused until …"). Press **Show the server's message** there to read exactly what the provider said. **Resume now** ends the pause at once and makes the waiting mail due again. A working **Test** also ends the pause and makes waiting mail due. A failed **Test** shows the reason but doesn't stop sorting, unless the provider says its limit is used up.

An answer that can't be read, or that names a rule that isn't on the list, counts as "no rule".

## Limits to know about

- One rule per mail.
- A small model gets some mail wrong. Start new rules in Suggest mode and switch them to Automatic once their suggestions look right.
- A run of rules covers at most 20,000 mails at a time.
- A Delete forever rule can't be Automatic.
- The Delete-forever record in the Activity log keeps the mail's sender, subject and date.
- Only new inbox mail is sorted automatically. Mail that arrives somewhere else (for example straight into a label by a Gmail filter) is not.

See also [mail backup](mail-backup.md), [security](security.md) and [known limitations](limitations.md).
