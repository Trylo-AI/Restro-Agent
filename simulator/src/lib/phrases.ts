// "Try typing" chips: synthetic phrases covering the dry-run scenarios (docs/rules.md#dry-run).
export const PHRASES: { group: string; items: string[] }[] = [
  {
    group: "Order",
    items: [
      "2 cold coffee aur ek avocado toast, toast mein chilli nahi",
      "2 flat whites and a croissant",
      "do masala chai aur ek poha",
      "दो कोल्ड कॉफी",
      "one cappucino pls",
      "the usual",
    ],
  },
  {
    group: "Change",
    items: ["make it 3 cold coffee", "remove the toast", "add oat milk to the latte", "cancel my order", "confirm"],
  },
  {
    group: "Ask",
    items: [
      "does the brownie have nuts?",
      "is the thai green curry gluten free?",
      "kuch healthy, protein wala, spicy nahi",
      "what's vegan here?",
      "how long for my order?",
    ],
  },
  { group: "People", items: ["talk to a person", "call waiter", "bill please", "this is taking forever, very bad service"] },
  { group: "Other", items: ["what's the cricket score?", "tell me a joke", "STOP"] },
];
