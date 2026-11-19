# How Mizan Works

## The Assistant and Draft Orders
Mizan is a consent-first investing assistant designed for educational simulations. When a user interacts with Mizan to evaluate their portfolio, the assistant analyzes the account state, compares current holdings against target allocations, and outlines necessary adjustments. However, the assistant cannot execute trades on its own. Instead, the assistant only prepares draft orders. These draft orders detail which assets would be purchased or sold to align the portfolio with the target. By keeping orders in draft status, the system ensures that automated conversations never trigger unexpected changes to account holdings.

## User Confirmation and Draft Expiry
Control always remains entirely with the user. Only the user can confirm an order set, and they do so by clicking the explicit confirm button in the user interface. The assistant does not possess a confirmation tool, which protects users from accidental executions or unexpected model behaviors. Furthermore, financial conditions in markets can evolve, so draft orders are temporary. Every draft order set expires automatically after 10 minutes. If the user does not click the confirm button before this 10-minute window closes, the draft becomes invalid, and a fresh order proposal must be prepared.

## Deterministic Code for All Calculations
A core design rule of Mizan is that all numbers come from code, never from the language model. While the language model is helpful for conversational explanations, summarizing concepts, and answering questions, it does not perform financial mathematics. All computations regarding asset prices, portfolio gaps, drift percentages, share quantities, and cash requirements are executed by a deterministic calculation engine. The language model merely reports the values provided by this deterministic engine. This strict separation guarantees that mathematical calculations remain precise, verifiable, and free from model hallucinations.

Educational simulation. This is not investment advice.
