# 数列的项、和与递推 · 教练解析

作答前不向学习者展示。候选错因不是学生事实；同组四题各有用途，通过某题仅支持其中实际检验的小目标。

## STEM-M2-SEQ-F

答案/评价要求：22

1. 第一项到第7项递增6次，不是7次。
2. a_7=4+6×3=22。

核查：
1. method：迭代；result：4,7,10,13,16,19,22
2. method：通项代回；result：a_n=3n+1，a_1=4且相邻差3，a_7=22

候选错因：1. code：E.sequence；hypothesis：把增加次数当7
2. code：E.language_condition；hypothesis：求前7项和而不是第7项。

## STEM-M2-SEQ-C

答案/评价要求：45

1. 这是逐项乘2，不是加2。
2. 前4项为3,6,12,24，S_4=45；末项24不等于和。

核查：
1. method：逐项求和；result：3+6+12+24=45
2. method：错位相减；result：2S_4−S_4=48−3=45

候选错因：1. code：E.sequence；hypothesis：把公比2误作公差2
2. code：E.language_condition；hypothesis：只答末项24。

## STEM-M2-SEQ-T

答案/评价要求：c_n=n²（n≥1）

1. 从第1项到第n项累加k=1至n−1的(2k+1)，不是累加到n。
2. 增量中1至n−1的和可首尾配对：1+(n−1)=n，共n−1项，和为n(n−1)/2；因此c_n=1+2(n−1)n/2+(n−1)=n²。
3. 递推差随n变化，不能套固定公差的通项。

核查：
1. method：候选公式代回及初值；result：1²=1；(n+1)²−n²=2n+1，初值与每步递推一致，唯一确定数列
2. method：五项迭代；result：1,4,9,16,25，与n²一致；这只是辅助核查，通项确认由代回完成

候选错因：1. code：E.sequence；hypothesis：求和上限错成n
2. code：E.strategy；hypothesis：把变化的增量当固定公差。

## STEM-M2-SEQ-D

答案/评价要求：168

1. 每列座位组成7,11,…；第8列7+7×4=35。
2. 8列总数=(7+35)×8/2=168。
3. 题目问总数，不是最后一列35。

核查：
1. method：直接列举求和；result：7+11+15+19+23+27+31+35=168
2. method：对称配对；result：4对每对42席，4×42=168

候选错因：1. code：E.representation；hypothesis：文字关系没有转成项数/首项/增量
2. code：E.language_condition；hypothesis：将第8列数答作总数。
