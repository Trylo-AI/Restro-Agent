import { dayMonth, inr, maskedPhone, ordinal } from "./format";

test("money from paise with Indian grouping", () => {
  expect(inr(124000)).toBe("₹ 1,240");
  expect(inr(12345)).toBe("₹ 123.45");
  expect(inr(12345678900)).toBe("₹ 12,34,56,789");
  expect(inr(0)).toBe("₹ 0");
});

test("phone numbers are always masked", () => {
  expect(maskedPhone("919876543421")).toBe("+91 98••• ••421");
  expect(maskedPhone(null)).toBe("••••");
});

test("ordinals", () => {
  expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(ordinal)).toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]);
});

test("dates read day-first in every browser locale", () => {
  expect(dayMonth("2026-10-12T10:00:00Z")).toBe("12 Oct");
});
