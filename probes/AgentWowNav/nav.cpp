// Read-only navmesh experiment inspired by the author's published helper.
// Does not connect to the game or move any character.
#include "DetourNavMesh.h"
#include "DetourNavMeshQuery.h"
#include "DetourAlloc.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <vector>

struct TileHeader {
    uint32_t magic, detour, mmap, size;
    unsigned char remainder[40];
};
static_assert(sizeof(TileHeader) == 56);

static double number(const char* text) {
    char* end = nullptr;
    double value = std::strtod(text, &end);
    if (!end || *end || !std::isfinite(value)) throw std::runtime_error("invalid coordinate");
    return value;
}

int main(int argc, char** argv) {
    try {
        if (argc != 9) throw std::runtime_error("usage: nav <mmaps-directory> <map-id> <x y z> <x y z>");
        std::filesystem::path root(argv[1]);
        int map = static_cast<int>(number(argv[2]));
        if (map < 0 || map > 999) throw std::runtime_error("map out of range");
        char name[64];
        std::snprintf(name, sizeof(name), "%03d.mmap", map);
        std::ifstream paramsFile(root / name, std::ios::binary);
        dtNavMeshParams params{};
        if (!paramsFile.read(reinterpret_cast<char*>(&params), sizeof(params))) throw std::runtime_error("missing or truncated mmap parameters");
        std::unique_ptr<dtNavMesh, decltype(&dtFreeNavMesh)> mesh(dtAllocNavMesh(), dtFreeNavMesh);
        if (!mesh || dtStatusFailed(mesh->init(&params))) throw std::runtime_error("navmesh init failed");
        // WoW (x,y,z) -> Detour (y,z,x).
        float a[3] = {float(number(argv[4])), float(number(argv[5])), float(number(argv[3]))};
        float b[3] = {float(number(argv[7])), float(number(argv[8])), float(number(argv[6]))};
        auto grid = [](float v) { return int(32 - v / 533.333333); };
        int ax = grid(a[2]), ay = grid(a[0]), bx = grid(b[2]), by = grid(b[0]);
        if (std::abs(ax-bx) > 8 || std::abs(ay-by) > 8) throw std::runtime_error("probe only supports bounded local routes");
        int loaded = 0, missing = 0;
        for (int x = std::max(0, std::min(ax,bx)-1); x <= std::min(63, std::max(ax,bx)+1); ++x) {
            for (int y = std::max(0, std::min(ay,by)-1); y <= std::min(63, std::max(ay,by)+1); ++y) {
                std::snprintf(name, sizeof(name), "%03d%02d%02d.mmtile", map, x, y);
                std::ifstream tile(root / name, std::ios::binary);
                if (!tile) { ++missing; continue; }
                TileHeader h{};
                if (!tile.read(reinterpret_cast<char*>(&h), sizeof(h)) || h.magic != 0x4d4d4150 || h.mmap != 19 || h.detour != DT_NAVMESH_VERSION || h.size < sizeof(dtMeshHeader) || h.size > (16u<<20)) throw std::runtime_error("tile header/version mismatch");
                auto data = static_cast<unsigned char*>(dtAlloc(h.size, DT_ALLOC_PERM));
                if (!data) throw std::runtime_error("allocation failed");
                if (!tile.read(reinterpret_cast<char*>(data), h.size)) { dtFree(data); throw std::runtime_error("truncated tile"); }
                if (dtStatusFailed(mesh->addTile(data, h.size, DT_TILE_FREE_DATA, 0, nullptr))) { dtFree(data); throw std::runtime_error("Detour tile rejected"); }
                ++loaded;
            }
        }
        std::unique_ptr<dtNavMeshQuery, decltype(&dtFreeNavMeshQuery)> query(dtAllocNavMeshQuery(), dtFreeNavMeshQuery);
        if (!query || dtStatusFailed(query->init(mesh.get(),10000))) throw std::runtime_error("query init failed");
        dtQueryFilter filter;
        filter.setIncludeFlags(1|2);
        filter.setExcludeFlags(0);
        float ext[3] = {5,12,5}, pa[3]{}, pb[3]{};
        dtPolyRef ra=0, rb=0;
        if (dtStatusFailed(query->findNearestPoly(a,ext,&filter,&ra,pa)) || dtStatusFailed(query->findNearestPoly(b,ext,&filter,&rb,pb)) || !ra || !rb) throw std::runtime_error("no endpoint polygon");
        dtPolyRef polygons[4096]; int n=0;
        dtStatus pathStatus = query->findPath(ra,rb,pa,pb,&filter,polygons,&n,4096);
        if (dtStatusFailed(pathStatus) || dtStatusDetail(pathStatus,DT_BUFFER_TOO_SMALL) || dtStatusDetail(pathStatus,DT_OUT_OF_NODES) || !n || polygons[n-1]!=rb) throw std::runtime_error("incomplete path");
        float pts[4096*3]; unsigned char flags[4096]; dtPolyRef refs[4096]; int count=0;
        dtStatus straight = query->findStraightPath(pa,pb,polygons,n,pts,flags,refs,&count,4096,DT_STRAIGHTPATH_ALL_CROSSINGS);
        if (dtStatusFailed(straight) || dtStatusDetail(straight,DT_BUFFER_TOO_SMALL) || !count) throw std::runtime_error("straight path failed or truncated");
        // Straight-path portal vertices may carry interpolated polygon-edge
        // heights rather than the local detail-mesh surface. Snap each vertex
        // to its own nearby surface before handing it to a movement controller.
        for (int i=0;i<count;++i) {
            dtPolyRef ref=0;float projected[3]{};
            float localExt[3]={0.15f,4.f,0.15f};
            if (dtStatusFailed(query->findNearestPoly(&pts[i*3],localExt,&filter,&ref,projected)) || !ref)
                throw std::runtime_error("waypoint surface projection failed");
            if (std::abs(projected[0]-pts[i*3])>.2f || std::abs(projected[2]-pts[i*3+2])>.2f)
                throw std::runtime_error("waypoint surface projection shifted horizontally");
            pts[i*3+1]=projected[1];
        }
        // Walk small steps on connected surfaces instead of re-projecting each
        // sample globally (which can jump between overlapping height layers).
        std::vector<float> surface;
        int boundarySnaps=0;
        float current[3]={pa[0],pa[1],pa[2]}; dtPolyRef currentRef=ra;
        surface.insert(surface.end(),current,current+3);
        for(int i=1;i<count;++i) {
            float* end=&pts[i*3];
            float dx=end[0]-current[0], dz=end[2]-current[2];
            int steps=std::max(1,int(std::ceil(std::sqrt(dx*dx+dz*dz)/0.5f)));
            float origin[3]={current[0],current[1],current[2]};
            for(int j=1;j<=steps;++j) {
                float goal[3]; for(int k=0;k<3;++k) goal[k]=origin[k]+(end[k]-origin[k])*float(j)/steps;
                float reached[3];dtPolyRef visited[64];int nv=0;
                dtStatus s=query->moveAlongSurface(currentRef,current,goal,&filter,reached,visited,&nv,64);
                if(dtStatusFailed(s)||dtStatusDetail(s,DT_BUFFER_TOO_SMALL)||!nv)
                    throw std::runtime_error("surface walk failed");
                for(int v=0;v<nv;++v)
                    if(std::find(polygons,polygons+n,visited[v])==polygons+n) {
                        // Only accept an endpoint boundary tie: the entire
                        // move prefix remains in the corridor, and the last
                        // polygon projects to the SAME point/height as the
                        // current corridor polygon. Do not follow the outside
                        // polygon on the next sample.
                        float onCurrent[3]{},onOther[3]{};bool overCurrent=false,overOther=false;
                        bool adjacent=false;
                        const dtMeshTile* tile=nullptr;const dtPoly* poly=nullptr;
                        if(dtStatusSucceed(mesh->getTileAndPolyByRef(currentRef,&tile,&poly)))
                            for(unsigned int link=poly->firstLink;link!=DT_NULL_LINK;link=tile->links[link].next)
                                if(tile->links[link].ref==visited[v])adjacent=true;
                        if(j==steps && v==nv-1 && adjacent
                           && dtStatusSucceed(query->closestPointOnPoly(currentRef,goal,onCurrent,&overCurrent))
                           && dtStatusSucceed(query->closestPointOnPoly(visited[v],goal,onOther,&overOther))
                           && std::hypot(onCurrent[0]-goal[0],onCurrent[2]-goal[2])<=.001f
                           && std::hypot(onOther[0]-goal[0],onOther[2]-goal[2])<=.001f
                           && std::abs(onOther[1]-onCurrent[1])<=.01f) {
                            std::copy(onCurrent,onCurrent+3,reached);
                            visited[nv-1]=currentRef;
                            ++boundarySnaps;
                            continue;
                        }
                        std::fprintf(stderr,"surface diagnostic: waypoint=%d sample=%d visited=%llu current=%llu wow_current=[%.5f,%.5f,%.5f] wow_goal=[%.5f,%.5f,%.5f]\n",
                            i,j,(unsigned long long)visited[v],(unsigned long long)currentRef,
                             current[2],current[0],current[1],goal[2],goal[0],goal[1]);
                        float other[3];bool inside=false;
                        query->closestPointOnPoly(visited[v],goal,other,&inside);
                        std::fprintf(stderr,"off-corridor projected height=%.6f inside=%d\n",other[1],inside);
                        for(int k=0;k<n;++k) {
                            float p[3];bool over=false;
                            if(dtStatusFailed(query->closestPointOnPoly(polygons[k],goal,p,&over)))continue;
                            float d=std::hypot(p[0]-goal[0],p[2]-goal[2]);
                            if(d<.2f)std::fprintf(stderr,"corridor candidate index=%d ref=%llu horizontal=%.8f height=%.6f inside=%d\n",
                                k,(unsigned long long)polygons[k],d,p[1],over);
                        }
                        throw std::runtime_error("surface walk left planned corridor");
                    }
                if(std::hypot(reached[0]-goal[0],reached[2]-goal[2])>0.1f)
                    throw std::runtime_error("surface walk blocked before sample");
                currentRef=visited[nv-1];float height=0;
                if(dtStatusFailed(query->getPolyHeight(currentRef,reached,&height))) {
                    // Portal boundary floating-point error: constrain the fix to
                    // the SAME visited polygon, never search another floor.
                    float closest[3];bool over=false;
                    if(dtStatusFailed(query->closestPointOnPoly(currentRef,reached,closest,&over))
                       ||std::hypot(closest[0]-reached[0],closest[2]-reached[2])>0.1f)
                        throw std::runtime_error("surface height unavailable");
                    std::copy(closest,closest+3,reached);height=closest[1];
                }
                reached[1]=height;
                if(std::abs(reached[1]-current[1])>2.f)
                    throw std::runtime_error("surface sample vertical discontinuity");
                std::copy(reached,reached+3,current);
                surface.insert(surface.end(),current,current+3);
            }
        }
        std::printf("{\"status\":\"PASS\",\"map\":%d,\"loaded_tiles\":%d,\"missing_tiles\":%d,\"path_polygons\":%d,\"waypoints\":[",map,loaded,missing,n);
        for (int i=0;i<count;++i) std::printf("%s[%.5f,%.5f,%.5f]",i?",":"",pts[3*i+2],pts[3*i],pts[3*i+1]);
        std::printf("],\"boundary_snaps\":%d,\"surface_samples\":[",boundarySnaps);
        for(size_t i=0;i<surface.size()/3;++i)
            std::printf("%s[%.5f,%.5f,%.5f]",i?",":"",surface[i*3+2],surface[i*3],surface[i*3+1]);
        std::printf("]}\n");
        return 0;
    } catch (const std::exception& e) {
        std::fprintf(stderr,"navigation probe: %s\n",e.what());
        return 1;
    }
}
