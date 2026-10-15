"""The lexicon (HOUSEHOLD_ASSISTANT_SPEC.md §17.4): concepts and the words and short phrases that mean them, in their
normalised form (normalise.py has already spelled out contractions and text-speak, so "what's" is "what is" here).

A word in a concept is matched as a whole word, phrases first and longest first; one word belongs to one concept
hit. Adding a word is one line here, with a line in tests/data/quick_corpus.tsv to prove it.
"""
from __future__ import annotations

CONCEPTS: dict[str, tuple[str, ...]] = {
    "TASK": ("task", "todo", "to do", "chore", "job", "errand", "thing to do", "things to do", "need to do",
             "have to do", "got to do", "gotta do", "pending", "left to do", "agenda", "plan", "plans", "my day",
             "going on", "on my plate", "what is left", "left for today", "anything left", "to do list",
             "todo list", "do i have", "have i got", "am i doing", "looking like", "look like", "my week",
             "do i need to", "busy", "anything on", "lined up", "planned", "due"),
    "LIST": ("list", "lists"),
    "SCHEDULE": ("schedule", "trash", "bin", "garbage", "recycling", "rubbish", "collection", "bin day",
                 "trash day", "garbage day", "maintenance", "service due", "filter", "routine", "household jobs"),
    "REMIND": ("remind", "reminder", "alert me", "ping me", "notify me", "do not let me forget", "nudge me"),
    "DONE": ("done", "finish", "complete", "tick off", "cross off", "mark done", "mark as done", "mark it done",
             "check off", "ticked off", "tick", "cross"),
    "SHOPPING": ("shopping", "groceries", "shopping list", "grocery list", "buy", "pick up", "get from the store",
                 "need from the shop", "need from the store", "market", "what should i buy", "what should we buy",
                 "what should i get", "what should i pick up", "what should we get", "groceries list",
                 "kirana", "supermarket", "store list", "to buy"),
    "PRICE": ("price", "prices", "cost", "how much is", "usually pay", "pay for", "how much did we pay",
              "how much did i pay", "how much do we pay", "what do we pay", "price check", "how much are", "cheap",
              "best place", "where to buy", "where can i buy", "where should i buy", "rate", "going for",
              "cheapest place", "best price"),
    "DEAL": ("deal", "offer", "discount", "discounted", "saving", "savings", "cheaper elsewhere", "price drop",
             "on sale", "bargain", "best prices", "save money", "save on", "cheaper pack", "cheaper option"),
    "BUDGET": ("budget", "on track", "overspent", "over budget", "how much is left", "left to spend", "under budget",
               "within budget", "within the budget", "under the budget", "over the budget"),
    "BIRTHDAY": ("birthday", "born", "turning", "anniversary", "wedding day", "celebrate", "celebrating"),
    "RELATION": ("related", "relation", "relative", "relationship", "family tree", "relation between"),
    "KIN": ("cousin", "uncle", "aunt", "auntie", "in law", "nephew", "niece", "grandfather", "grandmother",
            "grandpa", "grandma", "brother", "sister", "mother", "father", "son", "daughter", "wife", "husband",
            "brother in law", "sister in law", "mother in law", "father in law", "grandson", "granddaughter",
            "stepson", "stepdaughter", "great grandfather", "great grandmother", "nana", "nani", "dada", "dadi",
            "chacha", "chachi", "mama", "mami", "bua", "mausi", "bhabhi", "jiju"),
    "PERSON_INFO": ("how old is", "how old will", "age of", "where does", "where did", "who are", "who is",
                    "when was", "when is"),
    "SPEND": ("spend", "expense", "outgoing", "outgoings", "cost us", "splurge", "blow", "spend on",
              "money on", "spend at", "how much went"),
    "INCOME": ("income", "earn", "earnings", "salary", "came in", "money in", "how did we do", "summary",
               "overview", "net", "saved this month", "did we save", "how much did we save", "how are we doing",
               "how is the month going", "how are we doing this month"),
    "BILL": ("bill", "payment due", "payment", "emi", "renewal", "direct debit", "autopay", "due to pay",
             "to pay", "pay soon", "bill due", "bills due", "standing order"),
    "RECURRING": ("recurring", "subscription", "every month", "monthly charge", "regular payment",
                  "regular charge", "repeating", "repeat payment", "regularly", "every week"),
    "BALANCE": ("balance", "how much in", "how much do we have", "how much money do we have", "account",
                "credit card", "card", "statement", "in the bank", "bank balance", "money left"),
    "OWE": ("owe", "owe me", "i owe", "debt", "settle", "settle up", "square up", "even", "pay back",
            "who paid", "split", "splitpot", "tab", "iou", "pay who", "pay whom", "needs to pay", "need to pay who"),
    "SPLIT_ADD": ("split", "i pay", "we pay", "pay for", "add an expense", "add expense", "shared expense",
                  "chip in", "covered", "shared cost"),
    "RECENT": ("recent", "latest", "last few", "lately", "recently"),
    "CALORIE": ("calorie", "kcal", "cal", "macro", "protein", "carb", "fat", "eat today", "food today", "intake",
                "calorie goal", "how much have i eat", "how much did i eat", "diet", "nutrition"),
    "FOOD_LOG": ("log", "had", "eat", "i eat", "i had", "track", "record"),
    "MEAL": ("breakfast", "lunch", "dinner", "snack", "supper", "brunch"),
    "UNREAD": ("unread", "new message", "missed message", "anyone message", "any message", "message", "chat",
               "messaged", "dm", "inbox", "text"),
    "DOC": ("note", "document", "file", "manual", "warranty", "receipt pdf", "where is the", "where did i put",
            "look up", "search", "find", "pdf", "paperwork", "instructions", "guide"),
    "CHECKLIST": ("checklist", "packing list", "check list"),
    "SCORE": ("score", "high score", "leaderboard", "winning", "top score", "best at", "the record", "records",
              "record holder", "arcade",
              "who is best", "who is winning", "ranking", "game"),
    "MY": ("my", "mine", "i", "me"),
    "HOWMUCH": ("how much", "how much money"),
    "LISTS": ("my lists", "all lists", "all my lists", "which lists", "what lists", "lists do i have",
              "how many lists", "list of lists", "show lists", "our lists"),
    "ADD": ("add", "put", "stick", "throw in", "include", "put down", "write down", "jot down", "note down"),
    "NEED": ("we need", "i need", "need more", "we need more", "we are out of", "out of", "ran out of",
             "run out of", "running out of", "running low on", "low on", "get some", "buy some", "pick up some",
             "grab some", "we are low on", "almost out of", "used up"),
    # two months side by side: answered for spending and the month summary, else left to the model
    "COMPARE": ("compare", "compared to", "compared with", "comparison", "versus", "vs", "more than", "less than",
                "than", "difference between", "up or down", "gone up", "go up", "went up", "gone down", "go down",
                "went down", "higher than", "lower than", "against"),
    # a shopping list item bought ("got the milk", "bought eggs", "mark bread bought")
    "BOUGHT": ("bought", "have bought", "just bought", "mark bought", "mark as bought", "got the", "picked up",
               "purchased", "grabbed"),
    # a task moved to another day
    "MOVE": ("move", "push", "postpone", "snooze", "reschedule", "shift", "delay", "put off", "bump", "defer",
             "push back", "move it"),
    # a new note in Household Docs
    "NOTE_ADD": ("make a note", "take a note", "new note", "save a note", "write a note", "create a note",
                 "add a note", "note that", "make note"),
    "QUESTION_WORDS": ("why", "should", "should i", "should we", "would it", "which is better", "average", "predict",
                       "explain", "summarize", "afford", "recommend", "is it worth", "what if", "tell me about",
                       "how come"),
}

# what someone calls their family (§17.5): "my mum", "Ravi's wife" are passed to Family Tree in its own words
KIN_SPOKEN = {
    "mum": "mother", "mom": "mother", "mummy": "mother", "mommy": "mother", "mam": "mother", "ma": "mother",
    "maa": "mother", "amma": "mother", "ammi": "mother", "mother": "mother",
    "dad": "father", "daddy": "father", "papa": "father", "pappa": "father", "appa": "father", "abba": "father",
    "baba": "father", "pa": "father", "pop": "father", "father": "father",
    "hubby": "husband", "husband": "husband", "pati": "husband", "wife": "wife", "wifey": "wife", "missus": "wife",
    "biwi": "wife", "patni": "wife", "bro": "brother", "bhai": "brother", "bhaiya": "brother", "brother": "brother",
    "sis": "sister", "didi": "sister", "behen": "sister", "bahen": "sister", "sister": "sister", "son": "son",
    "beta": "son", "daughter": "daughter", "beti": "daughter", "grandma": "grandmother", "granny": "grandmother",
    "gran": "grandmother", "grandmother": "grandmother", "grandpa": "grandfather", "grandad": "grandfather",
    "granddad": "grandfather", "grandfather": "grandfather", "uncle": "uncle", "aunt": "aunt", "auntie": "aunt",
    "aunty": "aunt", "cousin": "cousin", "nephew": "nephew", "niece": "niece", "grandson": "grandson",
    "granddaughter": "granddaughter", "father in law": "father-in-law", "mother in law": "mother-in-law",
    "brother in law": "brother-in-law", "sister in law": "sister-in-law", "son in law": "son-in-law",
    "daughter in law": "daughter-in-law",
    "mums": "mother", "moms": "mother", "mummys": "mother", "dads": "father", "papas": "father",
    # the family's own words, as Family Tree knows them in its languages
    "nana": "nana", "nani": "nani", "dada": "dada", "dadi": "dadi", "chacha": "chacha", "chachi": "chachi",
    "mama": "mama", "mami": "mami", "bua": "bua", "mausi": "mausi", "bhabhi": "bhabhi", "jiju": "jiju",
}
# words that alone mean "my …" ("when is mum's birthday")
KIN_ALONE = {"mum", "mom", "mummy", "mommy", "mam", "maa", "amma", "ammi", "dad", "daddy", "papa", "pappa", "appa",
             "abba", "hubby", "wifey", "missus", "grandma", "granny", "gran", "grandpa", "grandad", "granddad", "nana",
             "nani", "dada", "dadi", "mums", "moms", "mummys", "dads", "papas"}

# the same thing by other names (§17.5): a word in a group finds a list item called by another word of it
ALIAS_GROUPS = (
    ("curd", "dahi", "yogurt", "yoghurt"), ("atta", "wheat flour", "chapati flour"),
    ("coriander", "dhania", "cilantro"), ("brinjal", "aubergine", "eggplant", "baingan"),
    ("okra", "bhindi", "lady finger"), ("chickpeas", "chana", "chole", "garbanzo"), ("spinach", "palak"),
    ("potato", "potatoes", "aloo"), ("onion", "onions", "pyaz"), ("tomato", "tomatoes", "tamatar"),
    ("milk", "doodh"), ("capsicum", "bell pepper", "shimla mirch"), ("paneer", "cottage cheese"),
    ("dal", "daal", "lentils"), ("maida", "plain flour", "all purpose flour"), ("sooji", "rava", "semolina"),
    ("jaggery", "gur"), ("turmeric", "haldi"), ("cumin", "jeera"), ("cardamom", "elaichi"), ("peas", "matar"),
    ("cauliflower", "gobi"), ("garlic", "lahsun"), ("ginger", "adrak"), ("sugar", "cheeni"), ("rice", "chawal"),
    ("eggs", "egg", "anda"), ("diapers", "nappies"), ("chips", "crisps"), ("cookies", "biscuits"),
    ("dish soap", "washing up liquid", "dishwashing liquid"), ("toilet paper", "toilet roll", "loo roll"),
    ("cling film", "plastic wrap"), ("aluminium foil", "foil", "tin foil"), ("soda", "fizzy drink", "soft drink"),
)

# what each concept is, in words, for the admin's Words ("Treat “chores” as tasks?")
CONCEPT_LABELS = {
    "TASK": "tasks", "LIST": "a list", "SCHEDULE": "the schedule (bins, routines)", "REMIND": "a reminder",
    "DONE": "ticking something off", "SHOPPING": "the shopping list", "PRICE": "a price", "DEAL": "deals",
    "BUDGET": "budgets", "BIRTHDAY": "birthdays", "RELATION": "how people are related", "KIN": "a family word",
    "PERSON_INFO": "about a person", "SPEND": "spending", "INCOME": "the month's income and summary",
    "BILL": "bills", "RECURRING": "recurring charges", "BALANCE": "account balances", "OWE": "who owes whom",
    "SPLIT_ADD": "splitting an expense", "RECENT": "recent", "CALORIE": "calories", "FOOD_LOG": "logging food",
    "MEAL": "a meal", "UNREAD": "messages", "DOC": "documents", "CHECKLIST": "a checklist", "SCORE": "game scores",
    "LISTS": "which lists there are", "ADD": "adding", "NEED": "running out of something", "COMPARE": "comparing",
    "BOUGHT": "bought", "MOVE": "moving to another day", "NOTE_ADD": "a new note",
}

# concepts whose words stay in the left-over text (a search or an item may contain them)
HELPERS = ("MY", "HOWMUCH")

# words left alone by the typo correction even though they aren't in a concept: everyday words, so "rice" never
# becomes "price" and "cart" never "card"
EVERYDAY = set("""
a an the and or but so if then than to of in on at by for from with without about into onto over under up down out
off near after before during until since this that these those here there what which who whom whose when where why
how is are was were be been being am do does did doing have has had having will would can could should shall may
might must not no yes yeah yep nope i me my mine we us our ours you your yours he him his she her hers they them
their theirs it its one ones some any all every each both few many much more most less least lot lots very really
just also too only even still again ever never always often sometimes now today tonight tomorrow yesterday week
month year day days morning afternoon evening night weekend weekday monday tuesday wednesday thursday friday
saturday sunday january february march april may june july august september october november december jan feb mar
apr jun jul aug sep sept oct nov dec next last this coming past previous upcoming soon later early late overdue
behind missed forgot forget please thanks thank hello hi hey okay ok sure maybe like want wanted would need want
got get go going gone come came make made take took give gave tell told say said see saw look looked know knew
think thought find found show showed ask asked let put keep kept call called try tried use used work worked feel
help start stop open close set left right new old big small good bad best worst first second third last next
other another same different own home house family kids kid wife husband mom mum dad mother father son daughter
brother sister baby people person someone anyone everyone nobody everybody somebody thing things stuff place
time times hour hours minute minutes am pm noon midnight o clock oclock half quarter
rice milk bread eggs egg butter cheese sugar salt oil ghee tea coffee water juice flour atta dal lentils beans
onion onions potato potatoes tomato tomatoes garlic ginger chili chilli pepper apple apples banana bananas orange
oranges mango grapes lemon lime carrot carrots spinach paneer curd yogurt yoghurt cream chicken fish meat pasta
noodles cereal oats biscuits cookies chips soap shampoo detergent tissue tissues toilet paper napkins foil wrap
bags bag batteries bulb bulbs nappies diapers wipes toothpaste toothbrush razor bleach vinegar honey jam peanut
cart car van bike bus train taxi petrol fuel diesel gas electric electricity rent mortgage water internet phone
mobile broadband tv netflix spotify amazon insurance school fees doctor dentist vet gym club
roti rotis chapati paratha naan idli dosa sambar rajma chole poha upma khichdi sabzi samosa pakora biryani pulao
salad sandwich pizza burger soup smoothie fruit veg vegetables
older younger oldest youngest remember live lives living lived parents parent married marry partner children
child son daughter brother sister mother father grandpa grandma uncle aunt cousin nephew niece law age old year
years turning turn anyone someone about call called tell telling now soon next whose who whom grand great step
fiance fiancee boyfriend girlfriend friend friends neighbour neighbor colleague boss
""".split())

# items a shopping list usually holds (a word alone that is one of these asks for its price and the list)
GROCERIES = set("""
milk bread eggs egg butter cheese sugar salt oil ghee tea coffee juice flour atta maida besan dal toor moong urad
masoor chana rajma lentils beans rice basmati poha sooji rava onion onions potato potatoes tomato tomatoes garlic
ginger chili chilli chillies pepper apple apples banana bananas orange oranges mango mangoes grapes lemon lemons lime
carrot carrots spinach palak methi coriander mint cucumber capsicum cabbage cauliflower peas okra bhindi brinjal
paneer curd dahi yogurt yoghurt cream chicken fish pasta noodles cereal oats biscuits cookies chips soap shampoo
detergent tissues napkins foil batteries nappies diapers wipes toothpaste bleach vinegar honey jam ketchup pickle
achar papad namkeen cornflakes muesli bananas strawberries blueberries avocado broccoli lettuce mushrooms
tofu almonds cashews raisins dates walnuts peanuts jaggery gur turmeric haldi cumin jeera mustard masala garam
cardamom elaichi cinnamon cloves bay salt sugar water dhania cilantro aubergine eggplant baingan chickpeas
garbanzo aloo pyaz tamatar doodh daal matar gobi lahsun adrak cheeni chawal anda nappies crisps
""".split())

# well-known stores and supermarkets: "how much did we spend at <store>" goes to Receipts, not the bank
STORES = set("""
costco walmart target aldi lidl tesco sainsburys sainsbury asda morrisons waitrose kroger safeway publix
wholefoods trader joes heb meijer wegmans dmart reliance bigbasket blinkit zepto instamart swiggy spar coop
iceland ocado more starbazaar natures basket kirana
""".split())

# spending categories people say; a Finance category of the household's own that contains the word wins
CATEGORIES = {
    "groceries": "Groceries", "food": "Food", "dining": "Dining", "eating out": "Eating out",
    "restaurants": "Restaurants", "restaurant": "Restaurants", "takeaway": "Takeaway", "takeout": "Takeaway",
    "fuel": "Fuel", "petrol": "Fuel", "gas": "Fuel", "transport": "Transport", "travel": "Travel",
    "utilities": "Utilities", "electricity": "Utilities", "water": "Utilities", "internet": "Internet",
    "phone": "Phone", "rent": "Rent", "mortgage": "Mortgage", "entertainment": "Entertainment",
    "health": "Health", "medical": "Medical", "pharmacy": "Pharmacy", "insurance": "Insurance",
    "education": "Education", "school": "Education", "kids": "Kids", "clothes": "Clothing", "clothing": "Clothing",
    "subscriptions": "Subscriptions", "shopping": "Shopping", "gifts": "Gifts", "household": "Household",
    "home": "Home", "pets": "Pets", "car": "Car", "holidays": "Holidays", "holiday": "Holidays",
    "personal care": "Personal care", "fitness": "Fitness", "gym": "Fitness", "charity": "Charity",
}

MEALS = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "supper": "dinner", "snack": "snack",
         "brunch": "lunch"}

# words the free-text parts (a search, an item, a name) never keep
STOPWORDS = set("""
a an the and or but so if then than to of in on at by for from with about into onto over up out off near
this that these those here there what which who whom whose when where why how is are was were be been being
am do does did have has had will would can could should may might must not my mine we us our ours you your
yours me i it its they them their any some all every each much many more most anything something everything
please thanks thank tell show find look give get got go going let know see want need like just also too only
now currently right again ever still yet there is there are
""".split())

ORDINALS = {"first": 0, "1st": 0, "1": 0, "top": 0, "second": 1, "2nd": 1, "2": 1, "other": 1, "third": 2,
            "3rd": 2, "3": 2, "last": -1}
YES = {"yes", "yeah", "yep", "sure", "ok", "okay", "please", "that", "that one", "this one"}


def all_words() -> set[str]:
    """Every single word the concepts use (the typo correction's targets)."""
    out = set()
    for phrases in CONCEPTS.values():
        for p in phrases:
            out.update(p.split())
    return out
