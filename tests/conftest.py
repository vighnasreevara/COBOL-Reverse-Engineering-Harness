import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixed(*code: str) -> list[str]:
    """Lay out program text in fixed format: columns 1-6 blank, 7 indicator, 8+ text.

    A line starting with '*', '-' or 'D' followed by a space puts that
    character in the indicator column.
    """
    out = []
    for line in code:
        if len(line) > 1 and line[0] in "*-D/" and line[1] == " ":
            out.append("      " + line[0] + line[2:])
        else:
            out.append("       " + line)
    return out


def write_mini_system(root: Path) -> Path:
    """A small COBOL system covering every input type, used for end-to-end tests."""
    def write(rel: str, lines: list[str]) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    write("cbl/ORDBATCH.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. ORDBATCH.",
        "ENVIRONMENT DIVISION.", "INPUT-OUTPUT SECTION.", "FILE-CONTROL.",
        "    SELECT ORDER-FILE ASSIGN TO ORDERS.",
        "    SELECT REPORT-FILE ASSIGN TO REPORT.",
        "DATA DIVISION.", "FILE SECTION.",
        "FD ORDER-FILE.", "    COPY ORDREC.",
        "FD REPORT-FILE.", "01 REPORT-LINE PIC X(80).",
        "WORKING-STORAGE SECTION.",
        "01 WS-EOF PIC X VALUE 'N'.", "   88 END-OF-ORDERS VALUE 'Y'.",
        "01 WS-TOTAL PIC S9(9)V99 COMP-3 VALUE 0.",
        "01 WS-ERR-MSG PIC X(40).",
        "PROCEDURE DIVISION.",
        "MAIN-PARA.",
        "    OPEN INPUT ORDER-FILE OUTPUT REPORT-FILE",
        "    PERFORM READ-ORDER",
        "    PERFORM PROCESS-ORDER UNTIL END-OF-ORDERS",
        "    CLOSE ORDER-FILE REPORT-FILE",
        "    GOBACK.",
        "READ-ORDER.",
        "    READ ORDER-FILE AT END SET END-OF-ORDERS TO TRUE END-READ.",
        "PROCESS-ORDER.",
        "    IF ORD-QTY <= ZERO",
        "        MOVE 'Quantity must be positive' TO WS-ERR-MSG",
        "        WRITE REPORT-LINE FROM WS-ERR-MSG",
        "    ELSE",
        "        COMPUTE WS-TOTAL ROUNDED = WS-TOTAL + ORD-QTY * ORD-PRICE",
        "        CALL 'PRICECHK' USING ORD-RECORD",
        "    END-IF",
        "    PERFORM READ-ORDER.",
    ))
    write("cbl/PRICECHK.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. PRICECHK.", "DATA DIVISION.", "LINKAGE SECTION.",
        "    COPY ORDREC.", "PROCEDURE DIVISION USING ORD-RECORD.",
        "    IF ORD-PRICE > 10000", "        MOVE 'H' TO ORD-STATUS", "    END-IF",
        "    GOBACK.",
    ))
    write("cbl/ORDINQ.cbl", fixed(
        "IDENTIFICATION DIVISION.", "PROGRAM-ID. ORDINQ.", "DATA DIVISION.", "WORKING-STORAGE SECTION.",
        "01 WS-KEY PIC X(8).", "01 WS-AMOUNT PIC S9(9)V99 COMP-3.", "PROCEDURE DIVISION.",
        "    EXEC CICS RECEIVE MAP('ORDMAP') MAPSET('ORDSET') END-EXEC",
        "    EXEC SQL SELECT AMOUNT INTO :WS-AMOUNT FROM ORDERS_T",
        "        WHERE ORDER_ID = :WS-KEY END-EXEC",
        "    EXEC CICS LINK PROGRAM('PRICECHK') END-EXEC",
        "    EXEC CICS SEND MAP('ORDMAP') MAPSET('ORDSET') END-EXEC",
        "    EXEC CICS RETURN TRANSID('ORDI') END-EXEC.",
    ))
    write("cpy/ORDREC.cpy", fixed(
        "01 ORD-RECORD.", "   05 ORD-ID PIC X(8).", "   05 ORD-QTY PIC S9(5) COMP-3.",
        "   05 ORD-PRICE PIC S9(7)V99 COMP-3.", "   05 ORD-STATUS PIC X.", "      88 ORD-HELD VALUE 'H'.",
    ))
    write("jcl/ORDJOB.jcl", [
        "//ORDJOB  JOB 1", "//STEP1   EXEC PGM=ORDBATCH",
        "//ORDERS  DD DSN=PROD.ORDERS,DISP=SHR",
        "//REPORT  DD DSN=PROD.ORDER.REPORT,DISP=(NEW,CATLG),DCB=(RECFM=FB,LRECL=80)",
    ])
    write("cics/orders.csd", ["DEFINE TRANSACTION(ORDI) PROGRAM(ORDINQ) GROUP(ORD)",
                              "DEFINE PROGRAM(ORDINQ) GROUP(ORD)", "DEFINE PROGRAM(PRICECHK) GROUP(ORD)"])
    write("maps/ORDSET.bms", ["ORDSET   DFHMSD TYPE=MAP,LANG=COBOL", "ORDMAP   DFHMDI SIZE=(24,80)",
                              "ORDKEY   DFHMDF POS=(2,1),LENGTH=8", "         DFHMSD TYPE=FINAL"])
    write("ddl/orders.sql", ["CREATE TABLE CUSTOMERS_T (CUST_ID CHAR(8) NOT NULL, PRIMARY KEY (CUST_ID));",
                             "CREATE TABLE ORDERS_T (ORDER_ID CHAR(8) NOT NULL, CUST_ID CHAR(8) NOT NULL,",
                             "  AMOUNT DECIMAL(11,2) NOT NULL, PRIMARY KEY (ORDER_ID),",
                             "  FOREIGN KEY (CUST_ID) REFERENCES CUSTOMERS_T(CUST_ID));"])
    return root
