# Human review draft — run `c7d7ddc3f2b2` / condition `C3`
- protocol: `qa-accuracy (real provider, LLM-judged)` ｜ judge: `openai_compat:deepseek-v4-pro` ｜ answered_by: `openai_compat:deepseek-flash`
- judged items: 30 ｜ 抽样规则见 `evals/human-review-template.md`（≤30 全判；>30 分层抽样 30）
- 评分锚点：1.0 完整且与参考一致 ｜ 0.5 部分正确 ｜ 0.0 错误/矛盾/编造/拒答但参考有答案 ｜ 弃判=参考可疑（出分母）
- 规则：先全部打完再看文末 judge 对照（防锚定）；换行以 ⏎ 标示，`|` 转义为 `\|`——
  原文以 result 文件为准。正式送审文件名应为 `human-review-<run_id>.md`（write-once）。

| # | qid | type | question | reference | answer（model 原文） | 人工分 | 备注 |
|---|-----|------|----------|-----------|----------------------|--------|------|
| 1 | locomo:conv-48:['D2:13', 'D4:34', 'D6:10', 'D19:17'] | single-hop | What places give Deborah peace? | sitting in a spot by the window in her Mom's house, sitting by the beach, Bali, forest trail in a nearby park | 我不知道，上文没有提到哪些地方能给 Deborah 带来平静。 |  |  |
| 2 | locomo:conv-48:['D1:2', 'D3:1', 'D4:5', 'D17:10', 'D17:12'] | single-hop | What kind of engineering projects has Jolene worked on? | electrical engineering, robotics, sustainable water purifier, productive and affordable aerial surveillance system | 根据现有对话内容，我无法得知Jolene参与过哪些类型的工程项目——对话中没有提到相关信息。 |  |  |
| 3 | locomo:conv-42:['D25:21', 'D25:23', 'D28:31'] | single-hop | What activities does Nate do with his turtles? | takes them onwalks, holds them,feeds themstrawberries, givesthem baths. | I do not know — the conversation above doesn’t mention any activities Nate does with turtles. |  |  |
| 4 | locomo:conv-42:['D2:3', 'D4:10', 'D5:1', 'D12:13', 'D12:14'] | single-hop | How many screenplays has Joanna written? | three | I don't know — the conversation doesn't mention how many screenplays Joanna has written. |  |  |
| 5 | locomo:conv-26:['D12:14', 'D8:4', 'D5:6'] | single-hop | What types of pottery have Melanie and her kids made? | bowls, cup | I don’t know — the conversation does not mention Melanie or her kids making pottery. |  |  |
| 6 | locomo:conv-50:['D26:6', 'D25:12', 'D12:2', 'D12:4'] | single-hop | Which events in Dave's life inspired him to take up auto engineering? | attending a car show with Dad, working on an old car in a neighbor's garage when he was young, spent a summer restoring an old car with Dad | I don’t know — the conversation above doesn’t mention anything about Dave’s life events or his reasons for taking up auto engineering. |  |  |
| 7 | locomo:conv-43:['D1:15', 'D12:18', 'D27:20'] | single-hop | What items does John collect? | sneakers, fantasy movie DVDs, jerseys | I don’t know—the conversation doesn’t mention what items John collects. |  |  |
| 8 | locomo:conv-43:['D3:19', 'D6:3', 'D9:6', 'D27:36'] | single-hop | Which cities has John been to? | Seattle, Chicago, New York, and Paris. | I don’t know. The conversation does not say which cities John has been to. |  |  |
| 9 | locomo:conv-43:['D13:1'] | multi-hop | Which week did Tim visit the UK for the Harry Potter Conference? | The week before October 13th, 2023. | I don't know — the conversation doesn't mention Tim visiting the UK or a Harry Potter Conference. |  |  |
| 10 | locomo:conv-47:['D31:2'] | multi-hop | When did John work with a game developer on a project? | November 5-6, 2022 | 我不知道，因为上述对话中并未提到 John 与游戏开发者合作项目的事情。 |  |  |
| 11 | locomo:conv-48:['D16:6', 'D28:26'] | multi-hop | When did Jolene adopt her snake Susie? | in 2021 | 我不知道，因为以上对话中没有提到 Jolene 收养蛇 Susie 的时间。 |  |  |
| 12 | locomo:conv-41:['D13:16'] | multi-hop | Who did Maria have dinner with on May 3, 2023? | her mother | I don’t know—the provided context does not mention who Maria had dinner with on May 3, 2023. |  |  |
| 13 | locomo:conv-30:['D6:1'] | multi-hop | When did Jon start to go to the gym? | March, 2023 | 上文没有提到Jon开始去健身房的时间，我无法确定。 |  |  |
| 14 | locomo:conv-47:['D16:9'] | multi-hop | When did James depart for his trip to Canada? | July 11, 2022 | 根据上文，我无法得知James何时出发去加拿大旅行。 |  |  |
| 15 | locomo:conv-48:['D14:4'] | multi-hop | When did Jolene buy a new aquarium for Seraphim? | 24 June, 2023 | 根据提供的对话内容，我无法得知乔琳何时为塞拉芬购买了新水族箱——对话中并未提到这件事。 |  |  |
| 16 | locomo:conv-48:['D29:4'] | multi-hop | When did Jolene and her partner try scuba diving lessons? | Friday before 17 September, 2023 | 我无法从提供的对话中得知——这段对话未提及 Jolene 和伴侣尝试潜水课的时间。 |  |  |
| 17 | locomo:conv-44:['D11:9'] | temporal | Which US state do Audrey and Andrew potentially live in? | Minnesota | I don’t know — the conversation does not mention which US state Audrey and Andrew live in. |  |  |
| 18 | locomo:conv-26:['D6:9'] | temporal | Would Caroline likely have Dr. Seuss books on her bookshelf? | Yes, since she collects classic children's books | I don’t know—the provided context does not mention Caroline’s bookshelf or any Dr. Seuss books. |  |  |
| 19 | locomo:conv-50:[] | temporal | Based on the conversation, did Calvin and Dave have a meeting in Boston between August and November 2023? Answer in yes or no. | No | No — the conversation only shows Calvin saying he'll catch Dave when he's in Boston, with no confirmation that a meeting actually took place. |  |  |
| 20 | locomo:conv-42:['D5:11', 'D2:23'] | temporal | What underlying condition might Joanna have based on her allergies? | asthma | 我不知道，因为上文没有提到 Joanna 的任何过敏信息或潜在疾病。 |  |  |
| 21 | locomo:conv-49:['D3:10', 'D3:15', 'D22:1', 'D8:17', 'D8:22', 'D9:8', 'D9:11', 'D14:7', 'D14:12', 'D12:7', 'D12:11'] | temporal | Considering their conversations and personal growth, what advice might Evan and Sam give to someone facing a major life transition or challenge? | Evan and Sam would likely advise embracing small, consistent changes​​, finding stress-relieving activities like hiking​​, painting, and road trips​​, and the importance of friendship and support in navigating challenges​​. | They might advise focusing on small joys when times are tough, believing “you’ve got this,” and reaching out to supportive people when you need to chat. They’d also remind someone to look after themselves. |  |  |
| 22 | locomo:conv-43:['D1:14', 'D1:16', 'D1:18'] | temporal | Would Tim enjoy reading books by C. S. Lewis or John Greene? | C. S.Lewis | I don’t know—the conversation only mentions Barcelona and doesn’t include any information about Tim’s reading preferences or those authors. |  |  |
| 23 | locomo:conv-43:['D28:1'] | open-domain | Where will Tim be going for a semester abroad? | Ireland | 我不知道。上文只提到巴塞罗那被推荐、蒂姆说会加入自己的清单，并未说明他学期交换将去哪里。 |  |  |
| 24 | locomo:conv-42:['D3:6'] | open-domain | What did Nate think of the coconut milk ice cream he made? | Super good, rich and creamy | Nate seemed to like it—he said he was glad Joanna liked it too and that the recipe “really jazzes it up.” The transcript does not explicitly mention “coconut milk ice cream,” though. |  |  |
| 25 | locomo:conv-48:['D6:4'] | open-domain | What gave Deborah peace in the garden she visited? | Roses and dahlias | 根据提供的对话内容，我无法得知 Deborah 在她所参观的花园里是什么让她感到平静——文中没有提到她参观花园或获得平静的相关信息。 |  |  |
| 26 | locomo:conv-41:['D3:1'] | open-domain | What kind of online group did John join? | service-focused online group | 根据提供的对话，我不知道约翰加入了什么样的在线群组——文中没有提到。 |  |  |
| 27 | locomo:conv-42:['D28:22'] | adversarial | What did Nate take a picture of near Fort Wayne last summer? |  | 我不知道——所提供的对话中没有提到 Nate 去年夏天在 Fort Wayne 附近拍过任何照片。 |  |  |
| 28 | locomo:conv-30:['D8:1'] | adversarial | Why did Gina shut down her bank account? |  | I don't know — the conversation doesn't mention Gina shutting down a bank account. |  |  |
| 29 | locomo:conv-48:['D28:7'] | adversarial | What did Jolene do with their mom's old friends? |  | I don’t know — the conversation does not mention Jolene’s mom’s old friends. |  |  |
| 30 | locomo:conv-43:['D14:7'] | adversarial | What does Tim find rewarding about mentoring the younger players? |  | The provided conversation doesn't mention Tim mentoring younger players, so I don't know what he finds rewarding about it. |  |  |

## ↓↓ judge 对照（评分期间勿看；打完后再核对并算一致率）↓↓

| qid | judge 分 | judge rationale |
|-----|----------|-----------------|
| locomo:conv-48:['D2:13', 'D4:34', 'D6:10', 'D19:17'] | 0.0 | The answer says the information is not mentioned, but the reference lists specific places that give Deborah peace. |
| locomo:conv-48:['D1:2', 'D3:1', 'D4:5', 'D17:10', 'D17:12'] | 0.0 | Answer fails to provide the projects and claims no information, contradicting the reference list. |
| locomo:conv-42:['D25:21', 'D25:23', 'D28:31'] | 0.0 | Answer says unknown, but reference lists specific activities: walks, holds, feeds strawberries, baths. |
| locomo:conv-42:['D2:3', 'D4:10', 'D5:1', 'D12:13', 'D12:14'] | 0.0 | The answer does not provide the correct number three; it says it doesn't know, which is incorrect. |
| locomo:conv-26:['D12:14', 'D8:4', 'D5:6'] | 0.0 | The reference states they made bowls and a cup, but the answer claims this is not mentioned. |
| locomo:conv-50:['D26:6', 'D25:12', 'D12:2', 'D12:4'] | 0.0 | The answer provides no events and contradicts the reference, which lists specific inspiring events. |
| locomo:conv-43:['D1:15', 'D12:18', 'D27:20'] | 0.0 | Answer says unknown, but reference specifies John collects sneakers, fantasy movie DVDs, and jerseys. |
| locomo:conv-43:['D3:19', 'D6:3', 'D9:6', 'D27:36'] | 0.0 | The answer says it does not know, but the reference provides the cities John has been to, so it is incorrect. |
| locomo:conv-43:['D13:1'] | 0.0 | The answer claims the information is not mentioned, but the reference provides the specific week; this contradicts the c |
| locomo:conv-47:['D31:2'] | 0.0 | Answer does not provide the date and claims it is not mentioned, contradicting the reference. |
| locomo:conv-48:['D16:6', 'D28:26'] | 0.0 | The answer does not provide the correct time (2021); it states the information is unknown. |
| locomo:conv-41:['D13:16'] | 0.0 | The answer says it does not know, but the reference states Maria had dinner with her mother, so it fails to provide the  |
| locomo:conv-30:['D6:1'] | 0.0 | The answer says the time is not mentioned, but the reference states it was March 2023. |
| locomo:conv-47:['D16:9'] | 0.0 | The answer says it cannot determine the date, contradicting the reference date of July 11, 2022. |
| locomo:conv-48:['D14:4'] | 0.0 | The answer does not provide the correct date and instead claims the information is unavailable, contradicting the refere |
| locomo:conv-48:['D29:4'] | 0.0 | The answer claims the time is not mentioned, but the reference states it was the Friday before 17 September, 2023. |
| locomo:conv-44:['D11:9'] | 0.0 | The reference identifies Minnesota, but the answer says the state is not mentioned, contradicting the correct answer. |
| locomo:conv-26:['D6:9'] | 0.0 | Answer contradicts reference by saying unknown; reference indicates likely yes. |
| locomo:conv-50:[] | 1.0 | Answer correctly says No and explains lack of confirmation. |
| locomo:conv-42:['D5:11', 'D2:23'] | 0.0 | The answer does not identify asthma and instead states the condition is unknown, contradicting the reference. |
| locomo:conv-49:['D3:10', 'D3:15', 'D22:1', 'D8:17', 'D8:22', 'D9:8', 'D9:11', 'D14:7', 'D14:12', 'D12:7', 'D12:11'] | 0.5 | Answer captures support from others and self-care, but misses the emphasis on small consistent changes and specific stre |
| locomo:conv-43:['D1:14', 'D1:16', 'D1:18'] | 0.0 | The reference states C.S. Lewis, but the answer says it does not know, which does not provide the correct response. |
| locomo:conv-43:['D28:1'] | 0.0 | The answer says it is unknown rather than giving Ireland, so it contradicts the reference. |
| locomo:conv-42:['D3:6'] | 0.5 | The answer captures that Nate liked it, but misses his specific description 'super good, rich and creamy' and instead gi |
| locomo:conv-48:['D6:4'] | 0.0 | The answer says it cannot be determined, but the reference states roses and dahlias gave Deborah peace. |
| locomo:conv-41:['D3:1'] | 0.0 | Answer says unknown/not mentioned, but reference states John joined a service-focused online group. |
| locomo:conv-42:['D28:22'] | 1.0 | The answer accurately states that the conversation does not mention Nate taking a picture near Fort Wayne last summer, m |
| locomo:conv-30:['D8:1'] | 1.0 | The answer matches the reference exactly. |
| locomo:conv-48:['D28:7'] | 1.0 | The answer correctly states the information is not mentioned, matching the reference. |
| locomo:conv-43:['D14:7'] | 1.0 | The answer correctly states the conversation does not mention Tim mentoring younger players, matching the reference. |
