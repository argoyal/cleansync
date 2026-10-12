# Mail rules

Mail rules sort your Gmail for you. You write each rule in your own words ("newsletters I never read: archive them"), and CleanSync asks an AI model of your choice which rule fits each new inbox mail. It can then archive the mail, add a label, mark it read, star it, or delete it (to Gmail's trash). Rules belong to one mailbox. CleanSync still never sends mail.

You choose the model: one running on your own machine (Ollama, LM Studio, llama.cpp, vLLM), your own LiteLLM gateway, or a hosted one such as OpenRouter's free models. No cloud AI service is needed.

## 1. Set up a model

Open **Settings → Models**.

1. Press **Add connection**. Give it a name and the server's address. The address usually ends in `/v1`. Some examples:
   - Ollama in the same Docker network: `http://ollama:11434/v1`
   - A LiteLLM gateway: `http://litellm:4000/v1`, with the gateway's virtual key as the API key
   - OpenRouter: `https://openrouter.ai/api/v1`, with your OpenRouter key. Pick a model whose name ends in `:free` if you don't want to pay.

   The API key and any extra headers are optional. Once saved they are never shown again (see [security](security.md)).
2. Under **Models for each task**, pick the connection and the model for each task:
   - **Sorting** decides which rule fits each mail.
   - **Writing rules** turns your sentence into a rule. When it isn't set, it uses the Sorting model.

   **List models** asks the server which models it has. Temperature 0 and a 60-second timeout are good defaults.
3. Press **Save**, then **Test**. Test sends one made-up mail (a weekly digest) and two made-up rules (Receipts and Newsletters) to the model. It shows how long the answer took, which rule it picked, and which way of answering worked. A good model picks Newsletters.

CleanSync talks to any server that speaks the OpenAI chat API. It first asks for a strict JSON answer; if the server can't do that, it asks for plain JSON, then for a plain line like `Rule: 2`. It remembers which one worked for each model.

## 2. Write rules

Open **Mail → Rules**. The rules are for the mailbox picked in the account switcher.

**From a sentence.** Type what you want in the box at the top, for example "Archive receipts from shop.example.com", and press **Write the rule**. The model fills in the rule form: a name, which mail it is for, what it does, and any fixed conditions it can see in your sentence. Check it and press **Save rule**. Nothing is saved before that. Without a model, the form opens with your sentence and you fill in the rest.

**From mail you picked.** In the mail list, tick some mails (or open one) and press **Make a rule…**. CleanSync fills in what the mails have in common (the same sender or domain, an unsubscribe link, a calendar invite), and the model writes a short description. Edit it and save.

**The rule form:**
- **Which mail is this for?** The description, in plain words. This is what the model reads.
- **What it does:** Archive, Mark read, Star, Label (the Gmail label is made if it doesn't exist), or Delete. Delete stands alone.
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

Turn on **Sort new inbox mail** at the top of the Rules page. From then on, each new mail that lands in the inbox is sorted a few minutes after the backup brings it in. Mail already in the inbox is left alone (see "Run rules on older mail" below).

What happens next depends on the rule's mode:
- **Suggest first:** nothing changes in Gmail. The mail gets a chip such as "Newsletters · suggested" and shows in the **Suggested** view in the Mail panel. Click the chip to see why, then **Approve** or **Correct…**. The Suggested view also has **Approve all**.
- **Automatic:** the rule acts in Gmail right away, and the chip says "applied".

Every change can be undone: click an "applied" chip and press **Undo**. Undo only puts back what CleanSync changed; anything you changed yourself since stays as you left it.

**Correct…** tells CleanSync which rule the mail should have had, or "No rule". An applied change is undone first, then the right rule acts. Corrections are passed to the model as hints for later mail from the same sender.

**Remembered senders.** When you approve or correct mail from the same sender into the same rule three times, CleanSync remembers that sender for that rule and stops asking the model about them. Remembered senders are listed on the rule, where you can remove each one.

**Your own moves win.** If CleanSync archived a mail and you put it back in the inbox, it is marked "moved back by you" and never sorted again. If you move a mail before approving its suggestion, the suggestion is dropped.

The Mail status line shows how sorting is going: how many mails were sorted today, how many are waiting, and how many suggestions there are.

## 4. Run rules on older mail

**Run rules on…** (on the Rules page, or above the mail list for the view or search you are in) runs your rules on mail you already have.

1. CleanSync decides every mail first, using only its own copy, and shows a preview: how many mails each rule would change, what it would do, and some examples with the model's reasons. Nothing has changed yet.
2. Type the number of mails to go ahead, and press **Change N mails**. Typing the number counts as your approval, so the changes are made whatever each rule's mode is.
3. The changes are made one by one. You can close the dialog; it keeps going. **Stop** stops it. A mail that changed between the preview and the run is skipped and listed in Suggested.

One run covers at most 20,000 mails. Narrow the search for more.

## 5. Delete and the Local archive

**Delete** (a rule action, or by hand from the reader or a selection) first checks CleanSync's copy of the mail, the same way an offload does. Then it moves the mail to Gmail's trash. Gmail empties its trash on its own after 30 days, and CleanSync can't empty it sooner (see [mail backup](mail-backup.md)).

CleanSync keeps its copy in the **Local archive**, a view in the Mail panel. Archived mail is kept apart: it doesn't show in the other views, their counts or the mail search. The Local archive has its own list and search.

From the Local archive you can:
- **Restore to Gmail:** the mail comes out of Gmail's trash, or is imported again if Gmail no longer has it.
- **Delete forever…:** type the number of mails, and CleanSync removes its own copy (the `.eml` file, its search entry and its rule history). This can't be undone. If Gmail no longer has the mail either, it exists nowhere. A short record (sender, subject, date) is kept in the Activity log.

If you take a deleted mail out of Gmail's trash yourself, CleanSync sees it on its next check and moves it back to your normal mail.

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

No mail is ever sorted on a guess. If the model can't be reached or doesn't answer in time, the mail simply waits and is tried again later, waiting a bit longer each time. After about an hour of failures, the Mail status line shows a warning with the reason. An answer that can't be read, or that names a rule that isn't on the list, counts as "no rule".

Rules with only fixed conditions, and remembered senders, keep working without a model.

## Limits to know about

- One rule per mail.
- A small model gets some mail wrong. Start new rules in Suggest mode and switch them to Automatic once their suggestions look right.
- A run of rules on older mail covers at most 20,000 mails at a time.
- The Delete-forever record in the Activity log keeps the mail's sender, subject and date.
- Only new inbox mail is sorted automatically. Mail that arrives somewhere else (for example straight into a label by a Gmail filter) is not.

See also [mail backup](mail-backup.md), [security](security.md) and [known limitations](limitations.md).
