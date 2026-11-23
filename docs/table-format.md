# otui table format

Table headers use TIMES attribute code names in short form, as declared in
the TIMES code.

## Rules

1. **One table per TIMES symbol** (set or parameter), named by its TIMES code
   name: `NCAP_COST`, `TOP`, `PRC`. Never a Veda alias (`INVCOST`). In a CSV
   export the file is `<symbol>.csv`; in a workbook, the sheet is `<symbol>`.
2. **Index columns** are headed by the symbol's domain as declared in the
   TIMES GAMS code, in order: `NCAP_COST(REG, ALLYEAR, PRC, CUR)` gives
   `REG, ALLYEAR, PRC, CUR`. The names come from TIMES.jl's reader
   (`manifest.json` of the dump), so they always match the TIMES version
   TIMES.jl translates (v4.9.7).
3. **Value columns:** parameters have `value`; sets have `element_text` (the
   optional text of a set element, usually empty).
4. **Special values** stay explicit. In DuckDB, `value__sv` holds the code
   (0 ordinary, 1 `EPS`, 2 `NA`, 3 `UNDF`; `INF` and `-INF` are ordinary
   numbers). In CSV and workbook exports they are written as text: `EPS`,
   `NA`, `UNDF`, `INF`, `-INF`.
5. **Cases the TIMES declarations leave open:**
   - A one-dimensional set names its column after itself, whatever its
     declared domain: `SET PRC(*)` gives `PRC`, and `SET COM(COM_GRP)` gives
     `COM`. Multi-dimensional symbols keep their
     declared domains.
   - Any other universe index (`*`) is `UNI` (11 cases in demo12, e.g.
     `COV_MAP(UNI, UNI_2)`).
   - A name repeated within one symbol takes a numeric suffix from the second
     use: `TOP_IRE(ALL_REG, COM, ALL_REG_2, COM_2, PRC)`,
     `ACT_CUM(REG, PRC, ITEM, ITEM_2, LIM)`.
   - A scalar (no index) has only `value`, e.g. `G_DYEAR`.
6. **Rows** are in the order TIMES.jl's reader gives them (GAMS order). otui
   never reorders a user's file on write-back.

7. **Workbooks** start with a `contents` sheet: every table's symbol, sheet
   name, kind (set or parameter), headers, record count and description.

## Not decided yet
- **A "SET" label for readability.** A
  set's table could carry a visible marker so a reader can tell a set from a
  parameter at a glance: for example the sheet or file name `SET_COM`, or a
  `SET` tag above the header row. Not implemented; until then the contents
  sheet gives each table's kind.
- Case of headers in user-made sheets: otui reads them case-insensitively
  and writes them upper case, as above (recommended).

## Appendix: demo12, every symbol with records

Generated with `otui headers` from TIMES_Demo `demo12` @ `962db86`, read by
TIMES.jl @ `b98cb31`.

| Symbol | Kind | Headers | Records | Description |
| --- | --- | --- | --- | --- |
| `ACL` | parameter | `value` | 1 |  |
| `ACTCG` | set | `ACTCG` | 1 |  |
| `ACT_BND` | parameter | `REG`, `ALLYEAR`, `PRC`, `ALL_TS`, `BND_TYPE`, `value` | 89 | Bound on activity of a process |
| `ACT_COST` | parameter | `REG`, `ALLYEAR`, `PRC`, `CUR`, `value` | 33 | Variable costs associated with activity of a process |
| `ACT_CUM` | parameter | `REG`, `PRC`, `ITEM`, `ITEM_2`, `LIM`, `value` | 18 | Bound on cumulative activity |
| `ACT_EFF` | parameter | `REG`, `ALLYEAR`, `PRC`, `COM_GRP`, `ALL_TS`, `value` | 259 | Activity efficiency for process |
| `AGE` | set | `AGE` | 200 | Age for SHAPEing |
| `AGEJ` | set | `J`, `AGE` | 1 |  |
| `ALLSOW` | set | `ALLSOW` | 96 | State-of-the-World |
| `ALLYEAR` | set | `ALLYEAR` | 242 | All Years |
| `ALL_REG` | set | `ALL_REG` | 4 | External + Internal Regions |
| `ALL_TS` | set | `ALL_TS` | 7 | The universe for time-slices |
| `ALTOBJ` | parameter | `value` | 1 |  |
| `ANNUAL` | set | `ANNUAL` | 1 | Annual identifier |
| `B` | parameter | `ALLYEAR`, `value` | 11 | Beginning year of each model period |
| `BDLOX` | set | `BDLOX` | 2 |  |
| `BDNEQ` | set | `BDNEQ` | 2 |  |
| `BDSIG` | parameter | `LIM`, `value` | 2 | Bound signum |
| `BDUPX` | set | `BDUPX` | 2 |  |
| `BEOH` | parameter | `UNI`, `value` | 2 | BOH / EOH offset |
| `BND_TYPE` | set | `BND_TYPE` | 3 | Bound Types |
| `BOHYEAR` | set | `BOHYEAR` | 242 | BOH + years |
| `CNT` | parameter | `value` | 1 |  |
| `COM` | set | `COM` | 70 | Commodities |
| `COM_AGG` | parameter | `REG`, `ALLYEAR`, `COM`, `COM_2`, `value` | 12 | Commodity aggregation parameter |
| `COM_DESC` | set | `REG`, `COM` | 138 | Region-based commodity descriptions |
| `COM_FR` | parameter | `REG`, `ALLYEAR`, `COM`, `ALL_TS`, `value` | 72 | Seasonal distribution of a commodity |
| `COM_GMAP` | set | `REG`, `COM_GRP`, `COM` | 142 | User groups of individual commodities |
| `COM_GRP` | set | `COM_GRP` | 111 | Commodities & Groups |
| `COM_IE` | parameter | `REG`, `ALLYEAR`, `COM`, `ALL_TS`, `value` | 2 | Seasonal efficiency of commodity |
| `COM_PEAK` | set | `REG`, `COM_GRP` | 1 | Peaking required flag |
| `COM_PKRSV` | parameter | `REG`, `ALLYEAR`, `COM`, `value` | 2 | Peaking reserve margin |
| `COM_PROJ` | parameter | `REG`, `ALLYEAR`, `COM`, `value` | 785 | Demand baseline projection |
| `COM_TMAP` | set | `REG`, `COM_TYPE`, `COM` | 138 | Primary grouping of commodities |
| `COM_TSL` | set | `REG`, `COM`, `TSLVL` | 10 | Level at which a commodity tracked |
| `COM_TYPE` | set | `COM_TYPE` | 5 | List of main commodity types groups |
| `COM_UNIT` | set | `REG`, `COM`, `UNITS_COM` | 138 | Units associated with each commodity |
| `COM_VAR` | set | `COM_VAR` | 2 |  |
| `COSTAGG` | set | `COSTAGG` | 24 | Types of cost aggregations |
| `COST_GMAP` | set | `COSTAGG`, `COSTAGG_2`, `UC_COST` | 46 |  |
| `COV_MAP` | set | `UNI`, `UNI_2` | 2 |  |
| `CUR` | set | `CUR` | 1 | Currencies = c$ |
| `CUR_GRP` | set | `CUR_GRP` | 4 | List of currency groups |
| `CVT` | set | `CVT` | 4 |  |
| `D` | parameter | `ALLYEAR`, `value` | 11 | Length of each period |
| `DATAYEAR` | set | `DATAYEAR` | 48 | Years for which user data is provided |
| `DEM_SECT` | set | `DEM_SECT` | 7 | List of demand sectors |
| `DFUNC` | parameter | `value` | 1 |  |
| `DONE` | parameter | `value` | 1 |  |
| `DUMIMP` | set | `DUMIMP` | 3 |  |
| `DUMP0` | parameter | `value` | 1 |  |
| `DUR_MAX` | parameter | `value` | 1 |  |
| `E` | parameter | `ALLYEAR`, `value` | 11 | Ending year of each model period |
| `ENDOFF` | parameter | `value` | 1 |  |
| `ENV_GRP` | set | `ENV_GRP` | 4 | List of emission groups |
| `EOHYEAR` | set | `EOHYEAR` | 243 | 'BOH/EOH' + years |
| `ERRLEV` | parameter | `value` | 1 |  |
| `F` | parameter | `value` | 1 |  |
| `FIN_GRP` | set | `FIN_GRP` | 3 | List of financial groups |
| `FIRST_VAL` | parameter | `value` | 1 |  |
| `FLO_SHAR` | parameter | `REG`, `ALLYEAR`, `PRC`, `COM`, `COM_GRP`, `ALL_TS`, `BND_TYPE`, `value` | 206 | Relationship between members of the same flow group |
| `G_CYCLE` | parameter | `TSLVL`, `value` | 3 | Number of cycles in average year |
| `G_DRATE` | parameter | `REG`, `ALLYEAR`, `CUR`, `value` | 2 | Discount rate for a currency |
| `G_DYEAR` | parameter | `value` | 1 | Year to discount to |
| `G_ILEDNO` | parameter | `value` | 1 | 1/threshold at which to ignore ILED |
| `G_NOINTERP` | parameter | `value` | 1 | Turn off interplation |
| `G_OVERLAP` | parameter | `value` | 1 | Overlap of stepped solutions (in years) |
| `G_TLIFE` | parameter | `value` | 1 | Default technology life if not provided |
| `G_UNCA` | parameter | `UNITS`, `UNITS_ACT`, `value` | 20 | Cap-to-Act conversions |
| `G_VINT` | parameter | `value` | 1 | % annual change in input data for vintaging |
| `G_YRFR` | parameter | `ALL_REG`, `ALL_TS`, `value` | 8 | Seasonal fraction of the year |
| `IFQ` | parameter | `value` | 1 |  |
| `IMP` | set | `IMP` | 1 | Imports |
| `IMPEXP` | set | `IMPEXP` | 2 | Imports/Exports |
| `IN_OUT` | set | `IN_OUT` | 2 | Input/Output |
| `IPS` | set | `IPS` | 2 |  |
| `IRE_PRICE` | parameter | `REG`, `ALLYEAR`, `PRC`, `COM`, `ALL_TS`, `ALL_REG`, `IMPEXP`, `CUR`, `value` | 66 | Exogenous price of import/export |
| `J` | set | `J` | 999 | Supply/demand steps 1*COM_STEP and SHAPE/MULTI |
| `JS` | set | `J`, `ALL_TS` | 1 |  |
| `LASTLL` | set | `LASTLL` | 1 |  |
| `LAST_VAL` | parameter | `value` | 1 |  |
| `LIM` | set | `LIM` | 4 | Limit Types |
| `LNX` | set | `LNX` | 2 |  |
| `M` | parameter | `ALLYEAR`, `value` | 11 | Middle year of each Period |
| `MATPRC` | set | `MATPRC` | 2 |  |
| `MAT_GRP` | set | `MAT_GRP` | 3 | List of material groups |
| `MAT_TYPE` | set | `MAT_TYPE` | 5 | List of material types |
| `MAXLIFE` | parameter | `value` | 1 |  |
| `MICRO` | parameter | `value` | 1 |  |
| `MILESTONYR` | set | `MILESTONYR` | 6 | Projection years for which model to be run |
| `MINYR` | parameter | `value` | 1 |  |
| `MIYR_BOH` | parameter | `value` | 1 |  |
| `MIYR_V1` | parameter | `value` | 1 |  |
| `MIYR_VL` | parameter | `value` | 1 |  |
| `MODLYEAR` | set | `MODLYEAR` | 13 | Years for which the model is to be run (MILESTONYR+PASTYEAR) |
| `MY_F` | parameter | `value` | 1 |  |
| `MY_FYEAR` | parameter | `value` | 1 |  |
| `MY_SUM` | parameter | `value` | 1 |  |
| `NCAP_AF` | parameter | `REG`, `ALLYEAR`, `PRC`, `ALL_TS`, `BND_TYPE`, `value` | 32 | Availability of capacity |
| `NCAP_AFA` | parameter | `REG`, `ALLYEAR`, `PRC`, `BND_TYPE`, `value` | 179 | Annual Availability of capacity |
| `NCAP_BND` | parameter | `REG`, `ALLYEAR`, `PRC`, `LIM`, `value` | 88 | Bound on overall capacity in a period |
| `NCAP_COST` | parameter | `REG`, `ALLYEAR`, `PRC`, `CUR`, `value` | 89 | Investment cost for new capacity |
| `NCAP_FOM` | parameter | `REG`, `ALLYEAR`, `PRC`, `CUR`, `value` | 103 | Fixed annual O&M costs |
| `NCAP_PASTI` | parameter | `REG`, `ALLYEAR`, `PRC`, `value` | 4 | Capacity install prior to study years |
| `NCAP_PKCNT` | parameter | `REG`, `ALLYEAR`, `PRC`, `ALL_TS`, `value` | 31 | Fraction of capacity contributing to peaking in time-slice TS |
| `NCAP_START` | parameter | `REG`, `PRC`, `value` | 87 | Start year for new investments |
| `NCAP_TLIFE` | parameter | `REG`, `ALLYEAR`, `PRC`, `value` | 249 | Technical lifetime of a process |
| `NRG_FORM` | set | `NRG_FORM` | 3 | List of energy forms |
| `NRG_GRID` | set | `NRG_GRID` | 6 | List of grid types |
| `NRG_TMAP` | set | `REG`, `NRG_TYPE`, `COM` | 10 | Grouping of energy carriers by type |
| `NRG_TYPE` | set | `NRG_TYPE` | 14 | List of energy types |
| `OBJ_C` | parameter | `value` | 1 |  |
| `OBJ_D` | parameter | `value` | 1 |  |
| `OBV` | set | `OBV` | 4 |  |
| `OPTFILEID` | parameter | `value` | 1 |  |
| `PASTYEAR` | set | `PASTYEAR` | 2 | Years before 1st MILESTONYR for which PASTI needs to be handled |
| `PG_SMAP` | set | `COM_GRP`, `J`, `COM_GRP_2` | 12 | Map from PG to SPG |
| `PRC` | set | `PRC` | 168 | Processes |
| `PRC_ACTFLO` | parameter | `REG`, `ALLYEAR`, `PRC`, `COM_GRP`, `value` | 48 | Convert from process activity to particular commodity flow |
| `PRC_ACTUNT` | set | `REG`, `PRC`, `COM_GRP`, `UNITS_ACT` | 335 | Primary commodity (or group) & activity unit |
| `PRC_CAPACT` | parameter | `REG`, `PRC`, `value` | 175 | Factor for going from capacity to activity |
| `PRC_DESC` | set | `REG`, `PRC` | 335 | Process descriptions by region |
| `PRC_GRP` | set | `PRC_GRP` | 19 | List of process groups |
| `PRC_MAP` | set | `REG`, `PRC_GRP`, `PRC` | 335 | Grouping of processes to nature |
| `PRC_RESID` | parameter | `REG`, `ALLYEAR`, `PRC`, `value` | 172 | Residual capacity available in each period |
| `PRC_RSOURC` | set | `PRC_RSOURC` | 7 | List of domestic resource supply groups |
| `PRC_TSL` | set | `REG`, `PRC`, `TSLVL` | 333 | Timeslice level for a process |
| `PRC_VINT` | set | `REG`, `PRC` | 2 | Process is to be vintaged |
| `PUTGRP` | parameter | `value` | 1 |  |
| `PUTOUT` | parameter | `value` | 1 |  |
| `PYR_V1` | parameter | `value` | 1 |  |
| `QASTAT` | set | `QASTAT` | 1 |  |
| `REG` | set | `REG` | 2 | Region |
| `RHS` | set | `RHS` | 1 |  |
| `SIDE` | set | `SIDE` | 2 | LHS and RHS of an equation |
| `SOLVESTAT` | set | `SOLVESTAT` | 19 |  |
| `STARTOFF` | parameter | `value` | 1 |  |
| `STL` | set | `STL` | 10 |  |
| `SUBT` | set | `SUBT` | 6 |  |
| `TOP` | set | `REG`, `PRC`, `COM`, `IN_OUT` | 654 | Topology for all process |
| `TOP_IRE` | set | `ALL_REG`, `COM`, `ALL_REG_2`, `COM_2`, `PRC` | 199 | Trade within area of study |
| `TSLVL` | set | `TSLVL` | 4 | Timeslice levels |
| `TSLVLNUM` | parameter | `TSLVL`, `value` | 4 | Timeslice level values |
| `TS_GROUP` | set | `ALL_REG`, `TSLVL`, `ALL_TS` | 14 | Timeslice Level assignment |
| `TS_MAP` | set | `ALL_REG`, `ALL_TS`, `ALL_TS_2` | 8 | Timeslice hierarchy tree: node+below |
| `UC_ANNUL` | set | `UC_ANNUL` | 3 |  |
| `UC_CAP` | parameter | `UC_N`, `SIDE`, `ALL_REG`, `ALLYEAR`, `PRC`, `value` | 12 | Multiplier of capacity variables |
| `UC_COMPRD` | parameter | `UC_N`, `SIDE`, `ALL_REG`, `ALLYEAR`, `COM`, `ALL_TS`, `value` | 4 | Multiplier of VAR_COMPRD variables |
| `UC_COST` | set | `UC_COST` | 5 | UC cost attributes |
| `UC_DYNT` | set | `UC_DYNT` | 4 |  |
| `UC_FLO` | parameter | `UC_N`, `SIDE`, `ALL_REG`, `ALLYEAR`, `PRC`, `COM`, `ALL_TS`, `value` | 30 | Multiplier of flow variables |
| `UC_GRPTYPE` | set | `UC_GRPTYPE` | 9 | Type of components within UC_GRP |
| `UC_MAPCOST` | set | `UC_COST`, `UC_NAME` | 10 | Compatibility map for cost attributes |
| `UC_N` | set | `UC_N` | 4 | Names of all manual constraints |
| `UC_NAME` | set | `UC_NAME` | 34 | Allowed parameters in user-constraints |
| `UC_NEWFLO` | set | `UC_NEWFLO` | 1 |  |
| `UC_NUMBER` | set | `UC_NUMBER` | 8 | Determines way of handling REG,T and TS |
| `UC_PERDS` | set | `UC_PERDS` | 2 |  |
| `UC_RHSRT` | parameter | `ALL_REG`, `UC_N`, `ALLYEAR`, `LIM`, `value` | 6 | Constant in user constraint |
| `UC_RHSRTS` | parameter | `ALL_REG`, `UC_N`, `ALLYEAR`, `ALL_TS`, `LIM`, `value` | 4 | Constant in user constraint |
| `UC_R_EACH` | set | `ALL_REG`, `UC_N` | 4 | Specification of regions, if UC_REG=EACH |
| `UC_SIGN` | parameter | `SIDE`, `value` | 2 | Sign of LHS and RHS expression |
| `UNIT` | set | `UNIT` | 101 | Number of different units |
| `UNITS` | set | `UNITS` | 26 | Units |
| `UNITS_ACT` | set | `UNITS_ACT` | 11 | Activity Units |
| `UNITS_CAP` | set | `UNITS_CAP` | 8 | Capacity Units |
| `UNITS_COM` | set | `UNITS_COM` | 11 | Commodity Units |
| `UNITS_MONY` | set | `UNITS_MONY` | 2 | Monatary Units |
| `UPT` | set | `UPT` | 3 | Start-up types |
| `VDA_EMCB` | parameter | `REG`, `ALLYEAR`, `COM`, `COM_2`, `value` | 38 | Combustion emission parameter (aka EMI_comb) |
| `XPT` | set | `XPT` | 1 | Exports |
| `YEARVAL` | parameter | `ALLYEAR`, `value` | 241 | Value of each year |
| `YR_V1` | parameter | `value` | 1 |  |
| `YR_VL` | parameter | `value` | 1 |  |
| `Z` | parameter | `value` | 1 |  |
