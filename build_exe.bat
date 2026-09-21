@echo off
REM ============================================================
REM  MEP-Parser 단일 .exe 빌드 스크립트
REM  - Python 설치된 개발 PC 에서 1회 실행 → dist\MEP-Parser.exe 생성
REM  - 생성된 exe 는 Python 없는 현장 PC 에서 더블클릭 실행
REM  - 권장 인터프리터: Python 3.11 (shapely/GEOS 휠 + PyInstaller 안정)
REM ============================================================
setlocal
cd /d "%~dp0"

REM Python 3.11 우선, 없으면 기본 python
py -3.11 -V >nul 2>&1 && (set "PY=py -3.11") || (set "PY=python")
echo [1/4] 빌드 의존성 설치 (%PY%)
REM ifcopenshell 은 선택 의존성이 아니라 **납품 경로**다(2026-09-21) — 빠지면 현장 PC 에서
REM '납품 검증 빌드 (IFC)' 가 죽고, IFC 재검사 테스트 21건이 게이트에서 조용히 skip 된다.
%PY% -m pip install --upgrade ezdxf shapely ifcopenshell pyinstaller pytest || goto :err

REM 깨진 .exe 를 만들지 않는다 — CI 없이 이 한 줄로 같은 효과를 낸다.
REM 게이트는 pytest 로 돌린다. `run_all.py` 는 픽스처를 못 주어 테스트 48개를
REM 건너뛰므로 게이트로 쓰기엔 반쪽이다(그것도 이제 스스로 실패로 끝난다).
echo [2/4] 테스트
%PY% -m pytest tests -q || goto :testerr

echo [3/4] PyInstaller 빌드
%PY% -m PyInstaller mep_parser.spec --noconfirm || goto :err

REM 번들 건전성 — pytest 는 개발 PC 의 site-packages 를 보므로 .exe 안에 뭐가 들어갔는지 모른다.
REM 여기서 납품 IFC 경로(ifc_builder + ifcopenshell.geom)가 동봉됐는지도 같이 드러난다.
echo [4/4] 번들 스모크 테스트
REM windowed(.exe) 라 stdout 이 없다 — start /wait 로 종료코드를 확실히 받는다.
start /wait "" "dist\MEP-Parser.exe" --selftest
if errorlevel 1 goto :smokeerr

echo.
echo ============================================================
echo  빌드 완료: dist\MEP-Parser.exe  (스모크 통과)
echo ============================================================
goto :eof

:smokeerr
echo.
echo [중단] 스모크 실패 — .exe 안에 빠진 모듈/리소스가 있습니다. selftest_result.txt 를 보세요.
exit /b 1

:testerr
echo.
echo [중단] 테스트 실패 — .exe 를 만들지 않습니다. 위 로그를 확인하세요.
exit /b 1

:err
echo.
echo [오류] 빌드 실패. 위 로그를 확인하세요.
exit /b 1
