// Ganesh et al., Disjoint Stable Matchings in Linear Time (WG 2021), Algorithm 2.
// Implementation for complete, strict, balanced preferences (the RPP setting).
// Copyright (c) 2026. SPDX-License-Identifier: MIT
//
// Input: n, followed by n rows of n women in each man's decreasing preference
// order, then n rows of n men in each woman's decreasing preference order.
// Indices are zero-based, unless --one-based is provided.
// --random n seed: generate uniformly randomized lists (no input).
// --cyclic n: cyclic instance with n edge-disjoint stable matchings.
// --identical n: common rankings on both sides (unique stable matching).
// --witness: output the matchings, one row each; default prints only r.
// --timings: time input/generation, preprocessing, main packing routine.
// --verify: check every output matching for stability/disjointness (O(r*n^2)).
// --skip-check: skip checking all preference lists are permutations.
//
// Time O(n^2), memory O(n^2); all integer lists are 32-bit.
// Extended GS stores each woman's reduced list as a prefix, and each man's
// reduced list with a forward-only cursor. An edge (m,w) remains present iff
// rankWoman[w][m] <= cutWoman[w] AND the woman occurs at/after cursorMan[m].
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

using u32 = uint32_t;
using Clock = std::chrono::steady_clock;

struct FastInput {
    static constexpr size_t BS = 1u << 16;
    unsigned char buf[BS]; size_t pos=0, len=0;
    int ch() {
        if (pos == len) { len=fread(buf,1,BS,stdin); pos=0; if(!len) return -1; }
        return buf[pos++];
    }
    bool readInt(u32& x) {
        int c;
        do { c=ch(); if(c==-1) return false; } while (c<=32);
        if(c<'0'||c>'9') throw std::runtime_error("Expected nonnegative decimal integer");
        uint64_t v=0;
        do { v=v*10+(c-'0'); if(v>UINT32_MAX) throw std::runtime_error("Integer overflows uint32"); c=ch(); }
        while(c>='0'&&c<='9');
        x=(u32)v; return true;
    }
};

struct Instance {
    u32 n;
    std::vector<u32> mp, wp; // men and women preference orders, row-major
    explicit Instance(u32 size):n(size), mp((size_t)n*n),wp((size_t)n*n){}
};

static void validate_and_rank(const std::vector<u32>& pref, u32 n,
                              std::vector<u32>& rank, bool validate) {
    rank.resize((size_t)n*n);
    for(u32 p=0;p<n;++p){
        size_t base=(size_t)p*n;
        for(u32 i=0;i<n;++i){
            u32 q=pref[base+i];
            if(q>=n) throw std::runtime_error("Out-of-range preference ID");
            rank[base+q]=i;
        }
        if(validate) for(u32 q=0;q<n;++q) {
            if(pref[base+rank[base+q]]!=q)
                throw std::runtime_error("Preference row is not a permutation");
        }
    }
}

// A woman-proposing GS on the ORIGINAL instance; each woman's best remaining
// man is queried using wp; men compare using inverse positions in mp.
static std::vector<u32> woman_optimal(const Instance& ins,
                                      const std::vector<u32>& rankMan) {
    const u32 n=ins.n; const u32 NO=n;
    std::vector<u32> next(n,0), manToWoman(n,NO), womanToMan(n,NO), stack(n);
    std::iota(stack.begin(),stack.end(),0u);
    while(!stack.empty()) {
        u32 w=stack.back(); stack.pop_back();
        size_t bw=(size_t)w*n;
        while(true){
            if(next[w]==n) throw std::runtime_error("GS failed on a complete profile");
            u32 m=ins.wp[bw+next[w]++];
            u32 prior=manToWoman[m];
            if(prior==NO || rankMan[(size_t)m*n+w] < rankMan[(size_t)m*n+prior]) {
                manToWoman[m]=w; womanToMan[w]=m;
                if(prior!=NO) { womanToMan[prior]=NO; stack.push_back(prior); }
                break;
            }
        }
    }
    return manToWoman;
}

// The storage of wp is recycled to hold the inverse WOMEN preference ranks;
// this saves one dense n x n array after the woman-optimal matching is found.
static void invert_wp_in_place(Instance& ins) {
    const u32 n=ins.n;
    std::vector<u32> tmp(n);
    for(u32 w=0;w<n;++w){
        size_t base=(size_t)w*n;
        for(u32 i=0;i<n;++i) tmp[ins.wp[base+i]]=i;
        std::copy(tmp.begin(),tmp.end(),ins.wp.begin()+base);
    }
}

struct Packing {
    u32 count=0;
    std::vector<std::vector<u32>> matchings;
};

// Runs GS-Extended on residual lists while retaining monotone reductions.
// Invariant: all men unengaged at start; all women's cutoffs describe the
// remaining prefixes after the previous GS-Extended/deletion iteration.
static void extended_gs(const Instance& ins, std::vector<u32>& cursor,
                        std::vector<int32_t>& cut, std::vector<u32>& match) {
    const u32 n=ins.n, NO=n;
    std::vector<u32> held(n,NO), stack(n);
    std::iota(stack.begin(),stack.end(),0u);
    std::fill(match.begin(),match.end(),NO);
    while(!stack.empty()) {
        u32 m=stack.back(); stack.pop_back();
        const size_t row=(size_t)m*n;
        while(cursor[m]<n) {
            u32 w=ins.mp[row+cursor[m]];
            // w already deleted m from its active prefix: skip permanently.
            if((int32_t)ins.wp[(size_t)w*n+m]>cut[w]) { ++cursor[m]; continue; }
            u32 old=held[w];
            u32 rank=ins.wp[(size_t)w*n+m];
            if(old!=NO && rank >= ins.wp[(size_t)w*n+old])
                throw std::runtime_error("Internal GS invariant violated");
            held[w]=m; match[m]=w;
            // Delete ALL worse men from w's list in O(1) using a prefix cut.
            cut[w]=(int32_t)rank;
            if(old!=NO) { match[old]=NO; stack.push_back(old); }
            break;
        }
        if(match[m]==NO) throw std::runtime_error("GS ran out of pairs: reduced instance has no full matching");
    }
}

static Packing ganesh(const Instance& ins, const std::vector<u32>& womanOpt,
                      bool witnesses) {
    const u32 n=ins.n;
    std::vector<u32> cursor(n,0), x(n,n);
    std::vector<int32_t> cut(n,(int32_t)n-1);
    Packing ans;
    for(;;){
        extended_gs(ins,cursor,cut,x);
        ++ans.count;
        if(witnesses) ans.matchings.push_back(x);
        bool overlaps=false;
        for(u32 m=0;m<n;++m) if(x[m]==womanOpt[m]) { overlaps=true; break; }
        if(overlaps) break; // Algorithm 2 includes the final overlapping X.
        // All x[m] are first on the respective man's reduced list and last
        // on the corresponding woman's reduced list (GS-Extended property).
        for(u32 m=0;m<n;++m) {
            u32 w=x[m];
            if(ins.mp[(size_t)m*n+cursor[m]]!=w ||
               (u32)cut[w]!=ins.wp[(size_t)w*n+m])
                throw std::runtime_error("Deletion invariant violated");
            ++cursor[m]; --cut[w];
        }
    }
    return ans;
}

static bool verify_matching(const Instance& ins, const std::vector<u32>& rankMan,
                            const std::vector<u32>& womRank,
                            const std::vector<u32>& x) {
    const u32 n=ins.n;
    std::vector<u32> partnerW(n,n);
    for(u32 m=0;m<n;++m) {
        if(x[m]>=n || partnerW[x[m]]!=n) return false;
        partnerW[x[m]]=m;
    }
    for(u32 m=0;m<n;++m){
        size_t bm=(size_t)m*n;
        for(u32 i=0;i<rankMan[bm+x[m]];++i) {
            u32 w=ins.mp[bm+i];
            if(womRank[(size_t)w*n+m] < womRank[(size_t)w*n+partnerW[w]]) return false;
        }
    }
    return true;
}

static Instance generate(u32 n, uint64_t seed, bool cyclic, bool identical) {
    Instance ins(n); std::mt19937_64 rng(seed);
    for(u32 p=0;p<n;++p) {
        auto fill=[&](std::vector<u32>& a, bool isWoman){
            size_t base=(size_t)p*n;
            for(u32 j=0;j<n;++j) a[base+j] = cyclic? (isWoman ? (p+1+j)%n : (p+j)%n) : j;
            if(!cyclic && !identical) {
                // Fisher--Yates with unbiased draw from mt19937_64.
                for(u32 j=n; j>1; --j) {
                    std::uniform_int_distribution<u32> dist(0,j-1);
                    std::swap(a[base+j-1],a[base+dist(rng)]);
                }
            }
        };
        fill(ins.mp,false); fill(ins.wp,true);
    }
    return ins;
}

static u32 parse_u32(const char* s){
    char* e=nullptr; unsigned long long v=strtoull(s,&e,10);
    if(e==s || *e || v==0 || v>UINT32_MAX) throw std::runtime_error("Invalid positive n");
    return (u32)v;
}
static uint64_t parse_u64(const char* s){
    char* e=nullptr; unsigned long long v=strtoull(s,&e,10);
    if(e==s || *e) throw std::runtime_error("Invalid seed");
    return (uint64_t)v;
}
static double elapsed(Clock::time_point x, Clock::time_point y){
    return std::chrono::duration<double,std::milli>(y-x).count();
}

int main(int argc,char** argv){
 try{
    bool witnesses=false, timings=false, verify=false, check=true, oneBased=false;
    int generator=0; u32 n=0; uint64_t seed=42;
    for(int i=1;i<argc;++i){
        std::string a=argv[i];
        if(a=="--witness") witnesses=true;
        else if(a=="--timings") timings=true;
        else if(a=="--verify") {verify=true; witnesses=true;}
        else if(a=="--skip-check") check=false;
        else if(a=="--one-based") oneBased=true;
        else if(a=="--random") {
            if(i+2>=argc) throw std::runtime_error("--random requires n and seed");
            generator=1; n=parse_u32(argv[++i]);seed=parse_u64(argv[++i]);
        } else if(a=="--cyclic" || a=="--identical") {
            if(i+1>=argc) throw std::runtime_error("generator requires n");
            generator=(a=="--cyclic"?2:3);n=parse_u32(argv[++i]);
        } else if(a=="--help") {
            std::cout << "Usage: ganesh_disjoint [--witness] [--verify] [--timings] [--one-based] [--skip-check]\n"
                         "       ganesh_disjoint --random N SEED [--timings] [--verify]\n"
                         "       ganesh_disjoint --cyclic N [--timings] [--verify]\n"
                         "       ganesh_disjoint --identical N [--timings] [--verify]\n"
                         "Input: N, then N men preference rows, then N women preference rows.\n"
                         "Output: r (maximum count of edge-disjoint stable matchings).\n";
            return 0;
        } else throw std::runtime_error("Unknown argument: "+a);
    }
    auto t0=Clock::now();
    Instance ins(0);
    if(generator) {
        if(n>INT32_MAX) throw std::runtime_error("n exceeds cutoff-index limit");
        ins=generate(n,seed,generator==2,generator==3);
    }
    else {
        FastInput rd;
        if(!rd.readInt(n) || n==0) throw std::runtime_error("Missing positive n");
        if((uint64_t)n*n > std::numeric_limits<size_t>::max()/sizeof(u32))
            throw std::runtime_error("n too large for address space");
        if(n>INT32_MAX) throw std::runtime_error("n exceeds cutoff-index limit");
        ins=Instance(n);
        auto readRows=[&](std::vector<u32>& a){
            for(size_t j=0;j<a.size();++j){
                u32 v;
                if(!rd.readInt(v)) throw std::runtime_error("Premature end of input");
                if(oneBased){if(v==0)throw std::runtime_error("Expected one-based index");--v;}
                if(v>=n)throw std::runtime_error("Input value out of range");
                a[j]=v;
            }
        };
        readRows(ins.mp); readRows(ins.wp);
    }
    auto t1=Clock::now();
    std::vector<u32> rankMan;
    validate_and_rank(ins.mp,n,rankMan,check);
    if(check) {
        // Women permutation check without allocating an additional n*n array.
        std::vector<u32> seen(n,0);
        for(u32 w=0;w<n;++w) {
            u32 token=w+1;
            for(u32 j=0;j<n;++j){
                u32 m=ins.wp[(size_t)w*n+j];
                if(m>=n || seen[m]==token) throw std::runtime_error("Invalid women's preferences");
                seen[m]=token;
            }
        }
    }
    std::vector<u32> wo=woman_optimal(ins,rankMan);
    invert_wp_in_place(ins);
    if(!verify) { std::vector<u32>().swap(rankMan); }
    auto t2=Clock::now();
    Packing p=ganesh(ins,wo,witnesses);
    auto t3=Clock::now();
    if(verify) {
        std::vector<uint8_t> used((size_t)n*n,0);
        for(const auto& x:p.matchings){
            if(!verify_matching(ins,rankMan,ins.wp,x)) throw std::runtime_error("Nonstable matching emitted");
            for(u32 m=0;m<n;++m){
                size_t id=(size_t)m*n+x[m];
                if(used[id]++) throw std::runtime_error("Matchings share an edge");
            }
        }
    }
    auto t4=Clock::now();
    std::cout << p.count << '\n';
    if(witnesses) for(auto& x:p.matchings){
        for(u32 i=0;i<n;++i) {
            if(i)std::cout << ' ';
            std::cout << x[i]+(oneBased?1u:0u);
        }
        std::cout << '\n';
    }
    if(timings) std::cerr << std::fixed << std::setprecision(3)
        << "n="<<n<<" r="<<p.count<<" generation_or_read_ms="<<elapsed(t0,t1)
        << " prep_ms="<<elapsed(t1,t2)<<" ganesh_ms="<<elapsed(t2,t3)
        << " verify_ms="<<elapsed(t3,t4)<<" total_ms="<<elapsed(t0,t4)<<'\n';
    return 0;
 } catch(const std::exception& ex) {
    std::cerr << "ERROR: "<<ex.what()<<'\n'; return 1;
 }
}
