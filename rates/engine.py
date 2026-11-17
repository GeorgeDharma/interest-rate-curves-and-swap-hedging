"""Two-curve EUR valuation with explicit cash flows and quote calibration.

The model uses ORE's published sample quotes and relevant conventions.
It does not call ORE, QuantLib, or a hosted pricing service.
"""
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from collections import defaultdict
from xml.etree import ElementTree as ET
import json, re
import numpy as np
from scipy.optimize import brentq
from .dates import add_tenor,adjust,advance_business,schedule,year_fraction

ASOF=date(2016,2,5)
BP=1e-4
ROOT=Path(__file__).resolve().parents[1]

def times(dates,asof=ASOF):
    return np.array([(d-asof).days/365 for d in dates],dtype=float)

@dataclass(frozen=True)
class Curve:
    """Log-linear discount factors. Projection factors are mathematical, not prices."""
    dates: tuple
    log_discounts: np.ndarray
    asof: date=ASOF

    def df(self,dates):
        scalar=isinstance(dates,date);xs=[dates] if scalar else dates
        t=times(xs,self.asof);nodes=times(self.dates,self.asof)
        if np.any(t< -1e-12) or np.any(t>nodes[-1]+1e-12):
            raise ValueError('Requested date is outside the calibrated curve.')
        out=np.exp(np.interp(t,nodes,self.log_discounts))
        return float(out[0]) if scalar else out

    def rolled(self,new_date):
        """Keep the original calendar-date curve and change its valuation origin."""
        origin=self.df(new_date)
        later=tuple(d for d in self.dates if d>new_date)
        return Curve((new_date,)+later,np.r_[0,np.log(self.df(later)/origin)],new_date)

@dataclass(frozen=True)
class Swap:
    trade_id: str
    start: date
    raw_end: date
    notional: float
    fixed_rate: float
    payer: int=1 # +1 means pay fixed and receive floating; -1 is the reverse.

    def __post_init__(self):
        if self.payer not in {-1,1}:raise ValueError('payer must be +1 or -1.')
        if self.notional<0 or not np.isfinite(self.notional):raise ValueError('Invalid notional.')
        if not np.isfinite(self.fixed_rate):raise ValueError('Invalid fixed rate.')
        if self.raw_end <= self.start:raise ValueError('Swap maturity must follow its start.')

    def fixed_schedule(self):return schedule(self.start,self.raw_end,12,'MF',0,'30U360')
    def float_schedule(self):return schedule(self.start,self.raw_end,6,'MF',0,'ACT360')

def swap_value(trade,discount,projection):
    fixed=trade.fixed_schedule();floating=trade.float_schedule()
    if discount.asof != projection.asof:
        raise ValueError('The curves must use the same valuation date.')
    if advance_business(min(a for a,_,_,_ in floating),-2)<discount.asof:
        raise ValueError('This case prices future-starting swaps; historical fixings are required for seasoned swaps.')
    annuity=sum(alpha*discount.df(pay) for _,_,pay,alpha in fixed)
    float_pv=sum((projection.df(a)/projection.df(b)-1)*discount.df(pay)
                 for a,b,pay,_ in floating)
    return trade.payer*trade.notional*(float_pv-trade.fixed_rate*annuity)

def par_rate(trade,discount,projection):
    fixed=trade.fixed_schedule();floating=trade.float_schedule()
    a=sum(alpha*discount.df(pay) for _,_,pay,alpha in fixed)
    floating_pv=sum((projection.df(s)/projection.df(e)-1)*discount.df(p) for s,e,p,_ in floating)
    return floating_pv/a

def fixed_annuity(trade,discount):
    return sum(alpha*discount.df(pay) for _,_,pay,alpha in trade.fixed_schedule())

def cashflows(trade,discount,projection):
    rows=[]
    for leg,periods in [('Fixed',trade.fixed_schedule()),('Floating',trade.float_schedule())]:
        sign=trade.payer*(-1 if leg=='Fixed' else 1)
        for a,b,pay,alpha in periods:
            rate=trade.fixed_rate if leg=='Fixed' else (projection.df(a)/projection.df(b)-1)/alpha
            amount=sign*trade.notional*alpha*rate
            rows.append(dict(trade_id=trade.trade_id,leg=leg,start=a.isoformat(),end=b.isoformat(),
                pay_date=pay.isoformat(),year_fraction=alpha,notional=trade.notional,sign=sign,
                rate=rate,projection_start=projection.df(a),projection_end=projection.df(b),
                discount_factor=discount.df(pay),cash_flow=amount,pv=amount*discount.df(pay)))
    return rows

@dataclass(frozen=True)
class Instrument:
    key:str
    curve:str
    kind:str
    rate:float
    start:date
    raw_end:date
    maturity:date
    line:int
    tenor:str

    @property
    def tenor_years(self):
        """Nominal quote tenor for scenario shapes and labelled maturity bands."""
        scale={'D':1/365,'W':7/365,'M':1/12,'Y':1}
        return sum(int(n)*scale[u] for n,u in re.findall(r'(\d+)([DWMY])',self.tenor))

def load_market(max_years=20):
    """Read only configured quotes; reject missing or duplicate selected keys."""
    source=ROOT/'data/source';observations=defaultdict(list)
    for line,s in enumerate((source/'market_20160205.txt').read_text().splitlines(),1):
        p=s.split()
        if len(p)==3 and p[0]=='20160205':observations[p[1]].append((float(p[2]),line))
    curves=ET.parse(source/'curveconfig.xml');items=[]
    spot=advance_business(ASOF,2)
    for curve in curves.findall('.//YieldCurve'):
        name=curve.findtext('CurveId')
        if name not in {'EUR1D','EUR6M'}:continue
        for segment in curve.findall('Segments/Simple'):
            for node in segment.findall('Quotes/Quote'):
                key=node.text;tenor=key.split('/')[-1]
                start=ASOF if key=='MM/RATE/EUR/0D/1D' else spot
                raw_end=advance_business(start,1) if key=='MM/RATE/EUR/0D/1D' else add_tenor(start,tenor)
                if (raw_end-start).days/365>max_years+.1:continue
                obs=observations[key]
                if len(obs)!=1:raise ValueError(f'Expected one quote for {key}; found {obs}')
                rate,line=obs[0]
                if not np.isfinite(rate):raise ValueError('Non-finite quote')
                kind=segment.findtext('Type')
                maturity=advance_business(adjust(raw_end,'Following'),1) if kind=='OIS' else adjust(raw_end)
                items.append(Instrument(key,name,kind,rate,start,raw_end,maturity,line,tenor))
    items.sort(key=lambda q:(0 if q.curve=='EUR1D' else 1,q.maturity))
    return tuple(items)

class Market:
    """Precompute schedules once; rebuild both curves for every market quote shock."""
    def __init__(self,instruments=None):
        self.instruments=load_market() if instruments is None else tuple(instruments)
        self.rates=np.array([i.rate for i in self.instruments])
        self._ois={}
        self._swaps={}
        for i in self.instruments:
            if i.kind=='OIS':self._ois[i.key]=schedule(i.start,i.raw_end,12,'Following',1,'ACT360')
            if i.kind=='Swap':self._swaps[i.key]=Swap(i.key,i.start,i.raw_end,1,0,1)

    def implied_quote(self,inst,discount,projection):
        c=discount if inst.curve=='EUR1D' else projection
        if inst.kind=='Deposit':
            end=adjust(inst.raw_end)
            return (c.df(inst.start)/c.df(end)-1)/year_fraction(inst.start,end)
        if inst.kind=='OIS':
            periods=self._ois[inst.key]
            numerator=sum((c.df(a)/c.df(b)-1)*c.df(p) for a,b,p,_ in periods)
            denominator=sum(alpha*c.df(p) for _,_,p,alpha in periods)
            return numerator/denominator
        if inst.kind=='Swap':return par_rate(self._swaps[inst.key],discount,projection)
        raise ValueError(inst.kind)

    def build(self,rates=None):
        quotes=self.rates if rates is None else np.asarray(rates,dtype=float)
        if quotes.shape!=self.rates.shape or not np.all(np.isfinite(quotes)):
            raise ValueError('The quote vector is incomplete or non-finite.')
        built={}
        for name in ['EUR1D','EUR6M']:
            dates=[ASOF];logd=[0.]
            for j,inst in enumerate(self.instruments):
                if inst.curve!=name:continue
                if inst.maturity<=dates[-1]:raise ValueError('Curve pillars must increase.')
                dates.append(inst.maturity)
                def error(last):
                    candidate=Curve(tuple(dates),np.array(logd+[last]))
                    dc=candidate if name=='EUR1D' else built['EUR1D']
                    return self.implied_quote(inst,dc,candidate)-quotes[j]
                root=brentq(error,-5,5,xtol=1e-14,rtol=1e-13)
                logd.append(root)
            built[name]=Curve(tuple(dates),np.array(logd))
        return built['EUR1D'],built['EUR6M']

    def values(self,trades,rates=None):
        d,p=self.build(rates)
        return np.array([swap_value(t,d,p) for t in trades])

    def pv01(self,trades,bump_bps=1):
        """Signed EUR change per +1 bp to one input quote, using central differences."""
        if bump_bps<=0:raise ValueError('Bump size must be positive.')
        out=np.zeros((len(self.rates),len(trades)))
        for i in range(len(self.rates)):
            shock=np.zeros(len(self.rates));shock[i]=bump_bps*BP
            out[i]=(self.values(trades,self.rates+shock)-self.values(trades,self.rates-shock))/(2*bump_bps)
        return out
