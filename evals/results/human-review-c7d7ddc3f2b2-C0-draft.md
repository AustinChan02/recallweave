# Human review draft — run `c7d7ddc3f2b2` / condition `C0`
- protocol: `qa-accuracy (real provider, LLM-judged)` ｜ judge: `openai_compat:deepseek-v4-pro` ｜ answered_by: `openai_compat:deepseek-flash`
- judged items: 30 ｜ 抽样规则见 `evals/human-review-template.md`（≤30 全判；>30 分层抽样 30）
- 评分锚点：1.0 完整且与参考一致 ｜ 0.5 部分正确 ｜ 0.0 错误/矛盾/编造/拒答但参考有答案 ｜ 弃判=参考可疑（出分母）
- 规则：先全部打完再看文末 judge 对照（防锚定）；换行以 ⏎ 标示，`|` 转义为 `\|`——
  原文以 result 文件为准。正式送审文件名应为 `human-review-<run_id>.md`（write-once）。

| # | qid | type | question | reference | answer（model 原文） | 人工分 | 备注 |
|---|-----|------|----------|-----------|----------------------|--------|------|
| 1 | locomo:conv-48:['D2:13', 'D4:34', 'D6:10', 'D19:17'] | single-hop | What places give Deborah peace? | sitting in a spot by the window in her Mom's house, sitting by the beach, Bali, forest trail in a nearby park | The context only mentions a gorgeous blossom tree near Deborah’s home that fills her with awe; it does not explicitly say which places give her peace. |  |  |
| 2 | locomo:conv-48:['D1:2', 'D3:1', 'D4:5', 'D17:10', 'D17:12'] | single-hop | What kind of engineering projects has Jolene worked on? | electrical engineering, robotics, sustainable water purifier, productive and affordable aerial surveillance system | I don’t know — the conversation does not mention any engineering projects Jolene has worked on. |  |  |
| 3 | locomo:conv-42:['D25:21', 'D25:23', 'D28:31'] | single-hop | What activities does Nate do with his turtles? | takes them onwalks, holds them,feeds themstrawberries, givesthem baths. | Nate bathes his turtles and takes them to the beach in Tampa, where he enjoys watching them soak in the sun. |  |  |
| 4 | locomo:conv-42:['D2:3', 'D4:10', 'D5:1', 'D12:13', 'D12:14'] | single-hop | How many screenplays has Joanna written? | three | 根据上文无法确定乔安娜写了几部剧本，文中只提到她的一部公路旅行剧本被拍成电影。 |  |  |
| 5 | locomo:conv-26:['D12:14', 'D8:4', 'D5:6'] | single-hop | What types of pottery have Melanie and her kids made? | bowls, cup | I don’t know — the conversation doesn’t mention Melanie and her kids making pottery. |  |  |
| 6 | locomo:conv-50:['D26:6', 'D25:12', 'D12:2', 'D12:4'] | single-hop | Which events in Dave's life inspired him to take up auto engineering? | attending a car show with Dad, working on an old car in a neighbor's garage when he was young, spent a summer restoring an old car with Dad | The provided conversation does not mention Dave taking up auto engineering or any events that inspired him to do so, so I don’t know. |  |  |
| 7 | locomo:conv-43:['D1:15', 'D12:18', 'D27:20'] | single-hop | What items does John collect? | sneakers, fantasy movie DVDs, jerseys | I don’t know — the conversation doesn’t mention what items John collects. |  |  |
| 8 | locomo:conv-43:['D3:19', 'D6:3', 'D9:6', 'D27:36'] | single-hop | Which cities has John been to? | Seattle, Chicago, New York, and Paris. | I don’t know—the conversation only shows John recommending Barcelona, not which cities he has actually visited. |  |  |
| 9 | locomo:conv-43:['D13:1'] | multi-hop | Which week did Tim visit the UK for the Harry Potter Conference? | The week before October 13th, 2023. | I don't know — the conversation doesn't mention Tim visiting the UK for a Harry Potter Conference. |  |  |
| 10 | locomo:conv-47:['D31:2'] | multi-hop | When did John work with a game developer on a project? | November 5-6, 2022 | I don’t know — the conversation does not mention John working with a game developer on a project. |  |  |
| 11 | locomo:conv-48:['D16:6', 'D28:26'] | multi-hop | When did Jolene adopt her snake Susie? | in 2021 | I don't know — the conversation doesn't mention Jolene adopting a snake named Susie. |  |  |
| 12 | locomo:conv-41:['D13:16'] | multi-hop | Who did Maria have dinner with on May 3, 2023? | her mother | 我不知道。提供的内容中没有提到玛丽亚在2023年5月3日与谁共进晚餐。 |  |  |
| 13 | locomo:conv-30:['D6:1'] | multi-hop | When did Jon start to go to the gym? | March, 2023 | The provided context does not mention Jon going to the gym, so I don’t know. |  |  |
| 14 | locomo:conv-47:['D16:9'] | multi-hop | When did James depart for his trip to Canada? | July 11, 2022 | I don’t know — the conversation does not mention a trip to Canada or when James departed. |  |  |
| 15 | locomo:conv-48:['D14:4'] | multi-hop | When did Jolene buy a new aquarium for Seraphim? | 24 June, 2023 | I don't know — the conversation doesn't mention Jolene buying a new aquarium for Seraphim. |  |  |
| 16 | locomo:conv-48:['D29:4'] | multi-hop | When did Jolene and her partner try scuba diving lessons? | Friday before 17 September, 2023 | I don’t know — the context does not mention Jolene and her partner trying scuba diving lessons. |  |  |
| 17 | locomo:conv-44:['D11:9'] | temporal | Which US state do Audrey and Andrew potentially live in? | Minnesota | I don’t know — the conversation doesn’t mention any US state or location for Audrey and Andrew. |  |  |
| 18 | locomo:conv-26:['D6:9'] | temporal | Would Caroline likely have Dr. Seuss books on her bookshelf? | Yes, since she collects classic children's books | Yes—Caroline is pursuing adoption and wants a safe, loving home for children, so children’s books like Dr. Seuss would likely fit on her bookshelf. |  |  |
| 19 | locomo:conv-50:[] | temporal | Based on the conversation, did Calvin and Dave have a meeting in Boston between August and November 2023? Answer in yes or no. | No | No. |  |  |
| 20 | locomo:conv-42:['D5:11', 'D2:23'] | temporal | What underlying condition might Joanna have based on her allergies? | asthma | I don’t know — the conversation doesn’t mention Joanna’s allergies or any underlying condition. |  |  |
| 21 | locomo:conv-49:['D3:10', 'D3:15', 'D22:1', 'D8:17', 'D8:22', 'D9:8', 'D9:11', 'D14:7', 'D14:12', 'D12:7', 'D12:11'] | temporal | Considering their conversations and personal growth, what advice might Evan and Sam give to someone facing a major life transition or challenge? | Evan and Sam would likely advise embracing small, consistent changes​​, finding stress-relieving activities like hiking​​, painting, and road trips​​, and the importance of friendship and support in navigating challenges​​. | They might advise staying positive while still taking care of yourself, spending time in nature, and finding joy in small moments because those little things add up. They’d also remind someone that supportive people are there to chat when needed. |  |  |
| 22 | locomo:conv-43:['D1:14', 'D1:16', 'D1:18'] | temporal | Would Tim enjoy reading books by C. S. Lewis or John Greene? | C. S.Lewis | I don’t know; the conversation does not mention C. S. Lewis, John Greene, or Tim’s reading preferences. |  |  |
| 23 | locomo:conv-43:['D28:1'] | open-domain | Where will Tim be going for a semester abroad? | Ireland | I don't know — the conversation doesn't mention where Tim will go for a semester abroad. |  |  |
| 24 | locomo:conv-42:['D3:6'] | open-domain | What did Nate think of the coconut milk ice cream he made? | Super good, rich and creamy | Nate seemed to like it—he said he was enjoying experimenting with flavors and that the recipe “really jazzes it up,” and he offered Joanna some. |  |  |
| 25 | locomo:conv-48:['D6:4'] | open-domain | What gave Deborah peace in the garden she visited? | Roses and dahlias | The context doesn’t say; Deborah only mentions watching a blossom tree near her home bloom each spring. |  |  |
| 26 | locomo:conv-41:['D3:1'] | open-domain | What kind of online group did John join? | service-focused online group | I don’t know—the context only mentions that John joined a fire-fighting brigade, not an online group. |  |  |
| 27 | locomo:conv-42:['D28:22'] | adversarial | What did Nate take a picture of near Fort Wayne last summer? |  | I don’t know — the context doesn’t mention Nate taking a picture near Fort Wayne last summer. |  |  |
| 28 | locomo:conv-30:['D8:1'] | adversarial | Why did Gina shut down her bank account? |  | I don’t know. The conversation does not mention Gina shutting down her bank account or why she would do so. |  |  |
| 29 | locomo:conv-48:['D28:7'] | adversarial | What did Jolene do with their mom's old friends? |  | 我不知道——对话中没有提到 Jolene 与母亲的老朋友做过什么。 |  |  |
| 30 | locomo:conv-43:['D14:7'] | adversarial | What does Tim find rewarding about mentoring the younger players? |  | I don’t know—the conversation doesn’t mention Tim mentoring younger players or finding that rewarding. |  |  |

## ↓↓ judge 对照（评分期间勿看；打完后再核对并算一致率）↓↓

| qid | judge 分 | judge rationale |
|-----|----------|-----------------|
| locomo:conv-48:['D2:13', 'D4:34', 'D6:10', 'D19:17'] | 0.0 | Answer says the context does not explicitly mention places that give Deborah peace, but the reference lists several such |
| locomo:conv-48:['D1:2', 'D3:1', 'D4:5', 'D17:10', 'D17:12'] | 0.0 | Answer claims no projects are mentioned, contradicting the reference which lists several engineering projects. |
| locomo:conv-42:['D25:21', 'D25:23', 'D28:31'] | 0.5 | Mentions giving baths correctly, but omits walks, holding, and feeding strawberries, and adds an unreferenced activity. |
| locomo:conv-42:['D2:3', 'D4:10', 'D5:1', 'D12:13', 'D12:14'] | 0.0 | The answer contradicts the reference by saying it cannot be determined, while the correct answer is three. |
| locomo:conv-26:['D12:14', 'D8:4', 'D5:6'] | 0.0 | The reference states they made bowls and a cup, but the answer says the conversation doesn't mention it. |
| locomo:conv-50:['D26:6', 'D25:12', 'D12:2', 'D12:4'] | 0.0 | The answer says no events are mentioned, contradicting the reference which lists specific inspiring events. |
| locomo:conv-43:['D1:15', 'D12:18', 'D27:20'] | 0.0 | The answer says the information is not mentioned, but the reference lists John's collected items: sneakers, fantasy movi |
| locomo:conv-43:['D3:19', 'D6:3', 'D9:6', 'D27:36'] | 0.0 | The answer says the cities are unknown and mentions Barcelona, contradicting the reference list of Seattle, Chicago, New |
| locomo:conv-43:['D13:1'] | 0.0 | Answer claims no mention, but reference provides the correct time. |
| locomo:conv-47:['D31:2'] | 0.0 | The answer says it does not know, but the reference gives November 5-6, 2022, so it fails to provide the correct informa |
| locomo:conv-48:['D16:6', 'D28:26'] | 0.0 | The answer says it does not know and does not provide the reference answer 'in 2021'. |
| locomo:conv-41:['D13:16'] | 0.0 | The answer states it doesn't know, but the reference says Maria had dinner with her mother, so it is wrong. |
| locomo:conv-30:['D6:1'] | 0.0 | The answer does not provide the reference date and claims no information, so it is incorrect. |
| locomo:conv-47:['D16:9'] | 0.0 | The reference gives the departure date as July 11, 2022, but the answer says it is not mentioned. |
| locomo:conv-48:['D14:4'] | 0.0 | The answer states the event is not mentioned, but the reference provides the date 24 June, 2023. |
| locomo:conv-48:['D29:4'] | 0.0 | The reference provides the time as Friday before 17 September 2023, but the answer denies any mention, contradicting the |
| locomo:conv-44:['D11:9'] | 0.0 | Answer says no state is mentioned, but the reference identifies Minnesota. |
| locomo:conv-26:['D6:9'] | 0.5 | The answer gives the correct yes, but the reasoning differs from the reference and is unsupported by it. |
| locomo:conv-50:[] | 1.0 | The answer matches the reference. |
| locomo:conv-42:['D5:11', 'D2:23'] | 0.0 | Answer fails to provide asthma and contradicts the reference by denying knowledge. |
| locomo:conv-49:['D3:10', 'D3:15', 'D22:1', 'D8:17', 'D8:22', 'D9:8', 'D9:11', 'D14:7', 'D14:12', 'D12:7', 'D12:11'] | 1.0 | The answer captures the key advice themes: small positive moments adding up, spending time in nature, and leaning on sup |
| locomo:conv-43:['D1:14', 'D1:16', 'D1:18'] | 0.0 | The answer does not provide the reference answer and claims lack of information, contradicting the correct answer of C.  |
| locomo:conv-43:['D28:1'] | 0.0 | Answer says not mentioned, but reference states Ireland. |
| locomo:conv-42:['D3:6'] | 0.5 | The answer conveys Nate liked the ice cream, but it does not mention his specific description of it as super good, rich  |
| locomo:conv-48:['D6:4'] | 0.0 | The answer contradicts the reference, which states roses and dahlias gave Deborah peace. |
| locomo:conv-41:['D3:1'] | 0.0 | Answer says John did not join an online group and contradicts the reference, which states he joined a service-focused on |
| locomo:conv-42:['D28:22'] | 1.0 | Answer matches reference: states unknown and no mention in context. |
| locomo:conv-30:['D8:1'] | 1.0 | The answer correctly states the information is not mentioned, matching the reference. |
| locomo:conv-48:['D28:7'] | 1.0 | Answer correctly states the dialogue does not mention Jolene doing anything with mom's old friends, consistent with the  |
| locomo:conv-43:['D14:7'] | 1.0 | The answer matches the reference exactly. |
